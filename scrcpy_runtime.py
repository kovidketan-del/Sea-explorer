"""Own a low-latency scrcpy mirror for safe, observable gameplay capture."""

from __future__ import annotations

import os
import subprocess
import time

from scrcpy_capture import CaptureUnavailable, ScrcpyCapture
from scrcpy_touch import _windows_user32
from scrcpy_binary import find_scrcpy


WINDOW_TITLE = "Sea Explorer Bot Mirror"


class ScrcpyRuntime:
    def __init__(self, adb, configured_path="", *, title=WINDOW_TITLE):
        self.adb = adb
        self.path = find_scrcpy(configured_path)
        self.title = title
        self.process = None
        self.capture_device = None

    def start(self):
        size = self.adb.run("shell", "wm", "size")
        import re
        match = re.search(r"(?:Override|Physical) size:\s*(\d+)x(\d+)", size)
        if not match:
            raise RuntimeError(f"Cannot read Android display size: {size!r}")
        width, height = map(int, match.groups())
        env = dict(os.environ, ADB=self.adb.adb)
        args = [
            str(self.path), f"--serial={self.adb.serial}", "--max-size=720",
            f"--window-title={self.title}", "--no-audio", "--render-driver=software",
        ]
        self.process = subprocess.Popen(
            args, cwd=str(self.path.parent), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.capture_device = ScrcpyCapture(self.title, (width, height))
        user32 = _windows_user32()
        deadline = time.monotonic() + 9.0
        last_error = None
        try:
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise RuntimeError(f"scrcpy exited at startup ({self.process.returncode})")
                hwnd = user32.FindWindowW(None, self.title)
                if hwnd:
                    if user32.GetForegroundWindow() != hwnd and not user32.SetForegroundWindow(hwnd):
                        # Windows denies cross-process focus from a background
                        # worker. A paired ALT press/release grants the normal
                        # foreground transition without leaving a key held.
                        user32.keybd_event(0x12, 0, 0, 0)
                        try:
                            user32.SetForegroundWindow(hwnd)
                        finally:
                            user32.keybd_event(0x12, 0, 2, 0)
                    try:
                        self.capture_device.capture()
                        return
                    except CaptureUnavailable as exc:
                        last_error = exc
                time.sleep(.10)
            raise RuntimeError(f"scrcpy mirror did not become capturable: {last_error}")
        except Exception:
            self.close()
            raise

    def capture(self):
        if self.process is None or self.process.poll() is not None:
            raise CaptureUnavailable("scrcpy video process stopped")
        return self.capture_device.capture()

    def close(self):
        if self.capture_device is not None:
            self.capture_device.close()
            self.capture_device = None
        process, self.process = self.process, None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
