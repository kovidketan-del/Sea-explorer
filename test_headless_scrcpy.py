"""Offline safety checks for the window-free scrcpy transport."""

import struct
import io
import subprocess
import time
import unittest

import numpy as np

from headless_scrcpy import HeadlessScrcpy, ScrcpySocketTouch, StreamUnavailable, _decoder_command
from sea_explorer_bot import ADB


class RecordingSocket:
    def __init__(self):
        self.messages=[]

    def sendall(self, data):
        self.messages.append(data)


class Runtime:
    def __init__(self):
        self.control_socket=RecordingSocket()


class InputSocket:
    def __init__(self, data):
        self.data=memoryview(data)
        self.offset=0

    def recv_into(self, view):
        count=min(len(view),len(self.data)-self.offset)
        if count:
            view[:count]=self.data[self.offset:self.offset+count]
            self.offset+=count
        return count


class SocketTouchTests(unittest.TestCase):
    def test_down_move_up_is_one_pointer_in_video_coordinates(self):
        runtime=Runtime()
        touch=ScrcpySocketTouch(runtime)
        touch.begin(162,480,324,720)
        touch.move_to(250,480,0,324,720)
        touch.release()
        self.assertEqual(len(runtime.control_socket.messages),3)
        parsed=[struct.unpack(">BBQIIHHHII",raw) for raw in runtime.control_socket.messages]
        self.assertEqual([message[1] for message in parsed],[0,2,1])
        self.assertEqual([(message[3],message[4]) for message in parsed],[(162,480),(250,480),(250,480)])
        self.assertTrue(all((message[5],message[6])==(324,720) for message in parsed))
        self.assertTrue(all(message[2]==parsed[0][2] for message in parsed))
        self.assertFalse(touch.active)

    def test_resize_releases_before_raising(self):
        runtime=Runtime()
        touch=ScrcpySocketTouch(runtime)
        touch.begin(100,200,324,720)
        with self.assertRaisesRegex(StreamUnavailable,"dimensions changed"):
            touch.move_to(150,200,30,400,720)
        self.assertEqual(len(runtime.control_socket.messages),2)
        self.assertFalse(touch.active)


class LatestFrameTests(unittest.TestCase):
    def test_ffmpeg_pipe_decodes_h264_without_window(self):
        from imageio_ffmpeg import get_ffmpeg_exe
        width,height=64,64
        raw=b"".join(np.full((height,width,3),i*20,np.uint8).tobytes()
                     for i in range(8))
        encoded=subprocess.run([
            get_ffmpeg_exe(),"-loglevel","error","-f","rawvideo",
            "-pix_fmt","bgr24","-s",f"{width}x{height}","-r","30",
            "-i","pipe:0","-frames:v","8","-c:v","libx264",
            "-preset","ultrafast","-tune","zerolatency","-f","h264","pipe:1",
        ],input=raw,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            check=True,timeout=10).stdout
        decoded=subprocess.run(_decoder_command("h264"),input=encoded,
                               stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                               check=True,timeout=10)
        self.assertGreaterEqual(len(decoded.stdout),width*height*3,
                                decoded.stderr.decode(errors="replace"))

    def test_returns_latest_without_building_frame_queue(self):
        runtime=HeadlessScrcpy.__new__(HeadlessScrcpy)
        import threading
        runtime._condition=threading.Condition()
        frame=np.zeros((2,3,3),np.uint8)
        runtime._latest=(frame,time.perf_counter(),time.perf_counter(),0)
        runtime._sequence=5
        runtime._last_delivered=0
        runtime._error=None
        self.assertIs(runtime.capture(timeout=0),frame)
        self.assertEqual(runtime._last_delivered,5)
        self.assertLess(runtime.last_frame_age,.1)
        preview=runtime.peek_frame()
        self.assertIsNot(preview,frame)
        self.assertEqual(runtime._last_delivered,5)

    def test_stream_error_fails_closed(self):
        runtime=HeadlessScrcpy.__new__(HeadlessScrcpy)
        import threading
        runtime._condition=threading.Condition()
        runtime._latest=None
        runtime._sequence=0
        runtime._last_delivered=0
        runtime._error=OSError("socket gone")
        with self.assertRaisesRegex(StreamUnavailable,"socket gone"):
            runtime.capture(timeout=0)

    def test_native_decoder_crash_is_contained_and_reported(self):
        runtime=HeadlessScrcpy.__new__(HeadlessScrcpy)
        import threading
        runtime._condition=threading.Condition()
        runtime._latest=None
        runtime._sequence=0
        runtime._last_delivered=0
        runtime._error=None
        runtime._closed=False
        runtime.video_size=(324,720)
        runtime._decoder_log=io.BytesIO(b"Windows fatal exception: access violation")
        runtime.decoder_process=type("Crashed",(),{
            "stdout":io.BytesIO(b""),
            "poll":lambda self: 3221225477,
        })()
        runtime._frame_loop()
        with self.assertRaisesRegex(StreamUnavailable,"access violation"):
            runtime.capture(timeout=0)

    def test_packet_headers_are_removed_before_ffmpeg_stdin(self):
        import threading
        config=b"\0\0\0\x01\x67\x42"
        frame=b"\0\0\0\x01\x65\x99"
        encoded=(struct.pack(">QI",1<<62,len(config))+config
                 +struct.pack(">QI",12345,len(frame))+frame)
        runtime=HeadlessScrcpy.__new__(HeadlessScrcpy)
        runtime.video_socket=InputSocket(encoded)
        sink=io.BytesIO()
        runtime.decoder_process=type("Decoder",(),{"stdin":sink})()
        runtime.metrics={"packets":0}
        runtime._closed=False
        runtime._condition=threading.Condition()
        runtime._error=None
        runtime._packet_loop()
        self.assertEqual(sink.getvalue(),config+frame)
        self.assertEqual(runtime.metrics["packets"],2)


class PhoneLockTests(unittest.TestCase):
    def test_keyguard_state_blocks_misclassified_gameplay(self):
        adb=ADB.__new__(ADB)
        adb.run=lambda *args,**kwargs: "KeyguardStateMonitor\n        mIsShowing=true\n        mInputRestricted=true"
        self.assertTrue(adb.is_keyguard_locked())
        adb.run=lambda *args,**kwargs: "KeyguardStateMonitor\n        mIsShowing=false"
        self.assertFalse(adb.is_keyguard_locked())


if __name__=="__main__":
    unittest.main()
