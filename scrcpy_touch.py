"""Continuous touch injection through the already-open scrcpy window.

This module maps device-frame coordinates into scrcpy's client area and uses
one Windows left-button press
for an entire sequence of moves.  It deliberately refuses to inject input if
the target window is hidden, covered, or loses focus.
"""

from __future__ import annotations

import ctypes
import math
import os
import time
from ctypes import wintypes


class TouchUnavailable(RuntimeError):
    """The scrcpy window cannot safely receive a drag."""


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


ULONG_PTR = ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else wintypes.DWORD


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", INPUT_UNION)]


INPUT_MOUSE = 0
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
GA_ROOT = 2


def _windows_user32():
    if os.name != "nt":
        raise TouchUnavailable("scrcpy touch control requires Windows")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
    user32.FindWindowW.restype = wintypes.HWND
    user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.IsIconic.argtypes = (wintypes.HWND,)
    user32.IsIconic.restype = wintypes.BOOL
    user32.GetClientRect.argtypes = (wintypes.HWND, ctypes.POINTER(RECT))
    user32.GetClientRect.restype = wintypes.BOOL
    user32.ClientToScreen.argtypes = (wintypes.HWND, ctypes.POINTER(POINT))
    user32.ClientToScreen.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.WindowFromPoint.argtypes = (POINT,)
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetCursorPos.argtypes = (ctypes.POINTER(POINT),)
    user32.GetCursorPos.restype = wintypes.BOOL
    user32.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
    user32.SetCursorPos.restype = wintypes.BOOL
    user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    user32.SendInput.restype = wintypes.UINT
    return user32


class ScrcpyTouch:
    """Hold a finger through successive observed passes in a scrcpy window.

    Coordinates are in the device frame's pixel space.  Pass its size to
    ``begin`` and ``move_to``, then ``release``
    at the end of gameplay.
    No Windows input occurs at construction time.
    """

    def __init__(
        self,
        window_title: str = "RMX3853",
        *,
        user32=None,
        clock=None,
        sleep=None,
        interval_ms: int = 14,
    ):
        self.user32 = user32 if user32 is not None else _windows_user32()
        self.window_title = window_title
        self.clock = clock or time.monotonic
        self.sleep = sleep or time.sleep
        if not 10 <= interval_ms <= 16:
            raise ValueError("interval_ms must be between 10 and 16")
        self.interval_ms = interval_ms
        self.active = False
        self._hwnd = None
        self._client = None
        self._point = None
        self._cursor_before = None
        self._last_cursor = None
        self.screen_size = None

    def set_screen_size(self, width: int, height: int) -> None:
        if width <= 0 or height <= 0:
            raise ValueError("screen size must be positive")
        size = (int(width), int(height))
        if self.active and size != self.screen_size:
            self.release()
            raise TouchUnavailable("screenshot size changed during held touch")
        self.screen_size = size

    def _window(self):
        u = self.user32
        hwnd = u.FindWindowW(None, self.window_title)
        if not hwnd or not u.IsWindowVisible(hwnd) or u.IsIconic(hwnd):
            raise TouchUnavailable(f"visible scrcpy window {self.window_title!r} unavailable")
        rect = RECT()
        if not u.GetClientRect(hwnd, ctypes.byref(rect)):
            raise TouchUnavailable("could not read scrcpy client rectangle")
        origin = POINT()
        if not u.ClientToScreen(hwnd, ctypes.byref(origin)):
            raise TouchUnavailable("could not locate scrcpy client rectangle")
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width < 100 or height < 100:
            raise TouchUnavailable("scrcpy client rectangle is too small")
        return hwnd, (origin.x, origin.y, width, height)

    def _screen_point(self, x: int, y: int, client) -> tuple[int, int]:
        left, top, client_width, client_height = client
        screen_width, screen_height = self.screen_size
        scale = min(client_width / screen_width, client_height / screen_height)
        pad_x = (client_width - screen_width * scale) / 2
        pad_y = (client_height - screen_height * scale) / 2
        x = max(0, min(screen_width - 1, int(x)))
        y = max(0, min(screen_height - 1, int(y)))
        return (
            min(left + client_width - 1, left + round(pad_x + x * scale)),
            min(top + client_height - 1, top + round(pad_y + y * scale)),
        )

    def _cursor(self) -> tuple[int, int]:
        point = POINT()
        if not self.user32.GetCursorPos(ctypes.byref(point)):
            raise TouchUnavailable("could not read mouse cursor")
        return point.x, point.y

    def _send_button(self, flag: int) -> None:
        event = INPUT(type=INPUT_MOUSE, u=INPUT_UNION(mi=MOUSEINPUT(dwFlags=flag)))
        if self.user32.SendInput(1, ctypes.pointer(event), ctypes.sizeof(INPUT)) != 1:
            raise TouchUnavailable("Windows did not accept mouse button event")

    def _check_target(self, client) -> None:
        hwnd, current_client = self._window()
        if hwnd != self._hwnd or current_client != client:
            raise TouchUnavailable("scrcpy window moved or resized during held touch")
        if self.user32.GetForegroundWindow() != hwnd:
            raise TouchUnavailable("scrcpy window lost focus")

    def _move_cursor(self, x: int, y: int, client) -> None:
        self._check_target(client)
        if self.active and self._last_cursor is not None:
            current = self._cursor()
            if math.dist(current, self._last_cursor) > 2:
                raise TouchUnavailable("mouse cursor moved outside bot control")
        px, py = self._screen_point(x, y, client)
        hovered = self.user32.WindowFromPoint(POINT(px, py))
        if not hovered or self.user32.GetAncestor(hovered, GA_ROOT) != self._hwnd:
            raise TouchUnavailable("scrcpy client is covered at planned touch point")
        if not self.user32.SetCursorPos(px, py):
            raise TouchUnavailable("could not move mouse cursor to scrcpy")
        self._last_cursor = (px, py)

    def begin(self, x: int, y: int, device_width: int, device_height: int) -> None:
        if self.active:
            raise TouchUnavailable("a touch is already held")
        self.set_screen_size(device_width, device_height)
        hwnd, client = self._window()
        self._hwnd = hwnd
        self._client = client
        try:
            if self.user32.GetForegroundWindow() != hwnd:
                if not self.user32.SetForegroundWindow(hwnd):
                    raise TouchUnavailable("could not focus scrcpy window")
            focus_deadline = self.clock() + 0.5
            while self.user32.GetForegroundWindow() != hwnd:
                remaining = focus_deadline - self.clock()
                if remaining <= 0:
                    break
                self.sleep(min(0.025, remaining))
            if self.user32.GetForegroundWindow() != hwnd:
                raise TouchUnavailable("scrcpy window is not focused")
            self._cursor_before = self._cursor()
            self._move_cursor(x, y, client)
            self.active = True
            self._send_button(MOUSEEVENTF_LEFTDOWN)
            self._point = (int(x), int(y))
        except Exception:
            self.release()
            raise

    def move_to(
        self, x: int, y: int, duration_ms: int, device_width: int, device_height: int
    ) -> None:
        if not self.active or self._point is None or self._client is None:
            raise TouchUnavailable("no touch is held")
        try:
            self.set_screen_size(device_width, device_height)
            if duration_ms <= 0:
                raise ValueError("duration_ms must be positive")
            end = (int(x), int(y))
            start = self._point
            steps = max(1, math.ceil(duration_ms / self.interval_ms))
            started = self.clock()
            for i in range(1, steps + 1):
                deadline = started + (duration_ms / 1000) * i / steps
                remaining = deadline - self.clock()
                if remaining > 0:
                    self.sleep(remaining)
                ratio = i / steps
                px = round(start[0] + (end[0] - start[0]) * ratio)
                py = round(start[1] + (end[1] - start[1]) * ratio)
                self._move_cursor(px, py, self._client)
            self._point = end
        except Exception:
            self.release()
            raise

    def drag(
        self,
        start: tuple[int, int],
        end: tuple[int, int],
        duration_ms: int,
        device_width: int,
        device_height: int,
    ) -> None:
        """Convenience gesture; ``begin``/``move_to`` support sustained play."""
        self.begin(*start, device_width, device_height)
        try:
            self.move_to(*end, duration_ms, device_width, device_height)
        finally:
            self.release()

    def release(self) -> None:
        was_active = self.active
        self.active = False
        error = None
        try:
            if was_active:
                self._send_button(MOUSEEVENTF_LEFTUP)
        except Exception as exc:
            error = exc
        finally:
            try:
                if self._cursor_before is not None and self._last_cursor is not None:
                    if math.dist(self._cursor(), self._last_cursor) <= 2:
                        self.user32.SetCursorPos(*self._cursor_before)
            except Exception:
                pass
            self._hwnd = None
            self._client = None
            self._point = None
            self._cursor_before = None
            self._last_cursor = None
        if error:
            raise error
