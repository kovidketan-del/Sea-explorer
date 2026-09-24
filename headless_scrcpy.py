"""Version-pinned scrcpy 4.1 video/control sockets without any desktop window.

The Android server emits H.264 access units over an ADB-forwarded TCP socket.
An isolated FFmpeg executable decodes those units into BGR arrays for OpenCV.
Only the newest decoded frame is retained, so vision cannot queue stale work.
The protocol is internal to scrcpy: this module intentionally requires the
matching bundled 4.1 server and rejects any other reported codec.
"""

from __future__ import annotations

from collections import deque
import secrets
import re
import socket
import struct
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import numpy as np

from scrcpy_binary import find_scrcpy


SERVER_VERSION = "4.1"
REMOTE_SERVER = "/data/local/tmp/sea-explorer-scrcpy-v4.1.jar"
MAX_PACKET = 8 * 1024 * 1024


def _decoder_command(codec):
    from imageio_ffmpeg import get_ffmpeg_exe
    input_codec="hevc" if codec=="h265" else "h264"
    return [
        get_ffmpeg_exe(),"-hide_banner","-loglevel","error",
        "-flags","low_delay",
        "-probesize","32768","-analyzeduration","0",
        "-f",input_codec,"-i","pipe:0",
        "-map","0:v:0","-an","-sn","-dn","-threads","1",
        "-fps_mode","passthrough","-pix_fmt","bgr24",
        "-f","rawvideo","pipe:1",
    ]


class StreamUnavailable(RuntimeError):
    pass


def _recv_exact(sock, count):
    parts = bytearray(count)
    view = memoryview(parts)
    offset = 0
    while offset < count:
        n = sock.recv_into(view[offset:])
        if n == 0:
            raise StreamUnavailable("scrcpy video socket closed")
        offset += n
    return parts


def _read_exact(stream, count):
    parts=bytearray(count)
    view=memoryview(parts)
    offset=0
    while offset<count:
        n=stream.readinto(view[offset:])
        if not n:
            raise StreamUnavailable("decoder output ended before a complete frame")
        offset+=n
    return parts


class HeadlessScrcpy:
    def __init__(self, adb, configured_path="", *, max_size=720, codec="h264", max_fps=0):
        if codec not in {"h264", "h265"}:
            raise ValueError("Only H.264 and H.265 are supported")
        self.adb = adb
        self.scrcpy_path = find_scrcpy(configured_path)
        self.server_path = self.scrcpy_path.parent / "scrcpy-server"
        if not self.server_path.is_file():
            raise FileNotFoundError(f"Matching scrcpy-server not found: {self.server_path}")
        self.max_size = int(max_size)
        self.codec = codec
        self.max_fps = int(max_fps)
        self.scid = secrets.randbelow(0x7fffffff)
        self.port = None
        self.process = None
        self._server_log = None
        self._decoder_log = None
        self.decoder_process = None
        self.video_socket = None
        self.control_socket = None
        self.video_size = None
        self.device_size = None
        self._thread = None
        self._frame_thread = None
        self._condition = threading.Condition()
        self._latest = None
        self._sequence = 0
        self._last_delivered = 0
        self._error = None
        self._closed = False
        self.metrics = {"packets": 0, "frames": 0, "decode_ms": deque(maxlen=2048), "convert_ms": deque(maxlen=2048), "packet_pts_us": None, "packet_received_at": None}

    def _connect(self, deadline, *, first=False):
        last_error = None
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise StreamUnavailable(f"scrcpy server exited during startup ({self.process.returncode})")
            try:
                sock=socket.create_connection(("127.0.0.1", self.port), timeout=.4)
                if first:
                    # adb forward accepts TCP even before the Android local
                    # socket exists. The server's dummy byte proves ownership.
                    try:
                        if sock.recv(1)!=b"\0":
                            raise StreamUnavailable("server not ready")
                    except Exception:
                        sock.close()
                        raise
                return sock
            except (OSError,StreamUnavailable) as exc:
                last_error = exc
                time.sleep(.06)
        raise StreamUnavailable(f"scrcpy socket did not open: {last_error}")

    def start(self):
        if self.process is not None:
            raise StreamUnavailable("headless scrcpy is already started")
        try:
            size=self.adb.run("shell","wm","size",timeout=5)
            match=re.search(r"(?:Override|Physical) size:\s*(\d+)x(\d+)",size)
            if not match:
                raise StreamUnavailable(f"Cannot read device display size: {size!r}")
            self.device_size=tuple(map(int,match.groups()))
            self.adb.run("push", str(self.server_path), REMOTE_SERVER, timeout=10)
            name=f"localabstract:scrcpy_{self.scid:08x}"
            self.port=int(self.adb.run("forward", "tcp:0", name, timeout=5).strip())
            args=[
                "shell", f"CLASSPATH={REMOTE_SERVER} app_process / com.genymobile.scrcpy.Server {SERVER_VERSION} "
                f"scid={self.scid:08x} tunnel_forward=true audio=false control=true "
                f"video_codec={self.codec} max_size={self.max_size} "
                f"max_fps={self.max_fps} send_device_meta=false "
                "clipboard_autosync=false log_level=warn",
            ]
            self._server_log=tempfile.TemporaryFile(mode="w+b")
            self.process=subprocess.Popen(
                self.adb._cmd(*args), stdout=self._server_log, stderr=self._server_log,
                **_hidden_process_options(),
            )
            deadline=time.monotonic()+8
            self.video_socket=self._connect(deadline,first=True)
            self.control_socket=self._connect(deadline)
            self.video_socket.settimeout(6)
            codec_id=bytes(_recv_exact(self.video_socket,4)).decode("ascii",errors="replace")
            if codec_id!=self.codec:
                raise StreamUnavailable(f"Expected {self.codec} stream, received {codec_id!r}")
            session=_recv_exact(self.video_socket,12)
            if not session[0]&0x80:
                raise StreamUnavailable("Missing scrcpy video session header")
            self.video_size=struct.unpack(">II",session[4:12])
            self.video_socket.settimeout(None)
            self._decoder_log=tempfile.TemporaryFile(mode="w+b")
            self.decoder_process=subprocess.Popen(
                _decoder_command(self.codec),
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self._decoder_log,
                **_hidden_process_options(),
            )
            self._thread=threading.Thread(target=self._packet_loop,name="SeaExplorer-Video-Packets",daemon=True)
            self._frame_thread=threading.Thread(target=self._frame_loop,name="SeaExplorer-Decoded-Frames",daemon=True)
            self._frame_thread.start()
            self._thread.start()
            # The first decoded frame establishes that the codec and transport
            # actually work; a successful socket connection alone is not enough.
            self.capture(timeout=6)
        except Exception as exc:
            details=self._server_diagnostics()
            if self.decoder_process is not None:
                details+=f"; decoder: {self._decoder_diagnostics()}"
            self.close()
            raise StreamUnavailable(f"{exc}; scrcpy server: {details}") from exc

    def _server_diagnostics(self):
        if self._server_log is None:
            return "no server log"
        try:
            self._server_log.flush()
            self._server_log.seek(0)
            return self._server_log.read(4000).decode(errors="replace")[-2000:]
        except OSError:
            return "server log unavailable"

    def _packet_loop(self):
        try:
            while not self._closed:
                header=_recv_exact(self.video_socket,12)
                if header[0]&0x80:
                    raise StreamUnavailable("scrcpy video session changed; stopping held touch safely")
                pts_flags,length=struct.unpack(">QI",header)
                if not 0<length<=MAX_PACKET:
                    raise StreamUnavailable(f"Invalid scrcpy packet length: {length}")
                payload=_recv_exact(self.video_socket,length)
                pipe=self.decoder_process.stdin
                if pipe is None:
                    raise StreamUnavailable("decoder stdin unavailable")
                pipe.write(payload)
                pipe.flush()
                self.metrics["packets"]+=1
        except Exception as exc:
            if not self._closed:
                with self._condition:
                    self._error=exc
                    self._condition.notify_all()

    def _frame_loop(self):
        try:
            output=self.decoder_process.stdout
            if output is None:
                raise StreamUnavailable("decoder stdout unavailable")
            w,h=self.video_size
            frame_bytes=w*h*3
            while not self._closed:
                raw=_read_exact(output,frame_bytes)
                array=np.frombuffer(raw,dtype=np.uint8).reshape((h,w,3))
                arrived=time.perf_counter()
                with self._condition:
                    self._latest=(array,arrived,arrived,None)
                    self._sequence+=1
                    self.metrics["frames"]+=1
                    self.metrics["packet_received_at"]=arrived
                    self._condition.notify_all()
        except Exception as exc:
            if not self._closed:
                with self._condition:
                    detail=self._decoder_diagnostics()
                    self._error=StreamUnavailable(f"decoder subprocess stopped: {exc}; {detail}")
                    self._condition.notify_all()

    def _decoder_diagnostics(self):
        process=self.decoder_process
        status=process.poll() if process is not None else None
        detail=f"exit={status}"
        if self._decoder_log is not None:
            try:
                self._decoder_log.flush()
                self._decoder_log.seek(0)
                detail+=" stderr="+self._decoder_log.read(3000).decode(errors="replace")[-1500:]
            except OSError:
                pass
        return detail

    def capture(self, timeout=.6):
        deadline=time.monotonic()+timeout
        with self._condition:
            while self._sequence==self._last_delivered and self._error is None:
                remaining=deadline-time.monotonic()
                if remaining<=0:
                    break
                self._condition.wait(remaining)
            if self._error is not None:
                raise StreamUnavailable(f"scrcpy video stream failed: {self._error}") from self._error
            if self._latest is None:
                raise StreamUnavailable("No decoded scrcpy frame arrived")
            self._last_delivered=self._sequence
            return self._latest[0]

    def peek_frame(self):
        """Copy the current image for optional diagnostics without consuming it."""
        with self._condition:
            return None if self._latest is None else self._latest[0].copy()

    @property
    def last_frame_age(self):
        with self._condition:
            if self._latest is None:
                return float("inf")
            return time.perf_counter()-self._latest[2]

    def close(self):
        self._closed=True
        decoder,self.decoder_process=self.decoder_process,None
        if decoder is not None:
            if decoder.poll() is None:
                decoder.terminate()
                try:
                    decoder.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    decoder.kill();decoder.wait(timeout=2)
        for name in ("video_socket","control_socket"):
            sock=getattr(self,name)
            setattr(self,name,None)
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                sock.close()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread=None
        if self._frame_thread is not None:
            self._frame_thread.join(timeout=2)
            self._frame_thread=None
        if decoder is not None:
            for stream in (decoder.stdin,decoder.stdout):
                if stream is not None:
                    stream.close()
        process,self.process=self.process,None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill();process.wait(timeout=2)
        if self.port is not None:
            try:
                self.adb.run("forward","--remove",f"tcp:{self.port}",check=False,timeout=3)
            except Exception:
                pass
            self.port=None
        if self._server_log is not None:
            self._server_log.close()
            self._server_log=None
        if self._decoder_log is not None:
            self._decoder_log.close()
            self._decoder_log=None


def _hidden_process_options():
    if hasattr(subprocess,"CREATE_NO_WINDOW"):
        return {"creationflags":subprocess.CREATE_NO_WINDOW}
    return {}


class ScrcpySocketTouch:
    """Persistent held finger over scrcpy's control socket, no desktop focus."""

    def __init__(self, runtime, *, interval_ms=12):
        self.runtime=runtime
        self.interval_ms=interval_ms
        self.position=None
        self.screen_size=None
        self.active=False
        self._lock=threading.Lock()

    @staticmethod
    def _message(action,x,y,w,h):
        # scrcpy 4.1 inject-touch-event: type, action, pointer-id, x/y,
        # reported screen size, pressure, action button, current buttons.
        pressure=0 if action==1 else 0xffff
        buttons=0 if action==1 else 1
        return struct.pack(">BBQIIHHHII",2,action,0xfffffffffffffffe,
                           int(x),int(y),int(w),int(h),pressure,buttons,buttons)

    def _send(self,action,x,y,w,h):
        sock=self.runtime.control_socket
        if sock is None:
            raise StreamUnavailable("scrcpy control socket is closed")
        with self._lock:
            sock.sendall(self._message(action,x,y,w,h))

    def begin(self,x,y,w,h):
        if self.active:
            raise StreamUnavailable("touch is already held")
        self._send(0,x,y,w,h)
        self.position=(int(x),int(y))
        self.screen_size=(int(w),int(h))
        self.active=True

    def move_to(self,x,y,duration_ms,w,h):
        if not self.active or self.position is None:
            raise StreamUnavailable("no touch is held")
        if (int(w),int(h))!=self.screen_size:
            self.release()
            raise StreamUnavailable("video dimensions changed during held touch")
        start=self.position
        end=(int(x),int(y))
        steps=max(1,int(duration_ms/self.interval_ms))
        started=time.perf_counter()
        for i in range(1,steps+1):
            target=started+duration_ms/1000*i/steps
            delay=target-time.perf_counter()
            if delay>0:
                time.sleep(delay)
            ratio=i/steps
            point=(round(start[0]+(end[0]-start[0])*ratio),
                   round(start[1]+(end[1]-start[1])*ratio))
            self._send(2,*point,w,h)
        self.position=end

    def release(self):
        position=self.position
        size=self.screen_size
        self.active=False
        self.position=None
        self.screen_size=None
        if position is not None and size is not None:
            self._send(1,*position,*size)
