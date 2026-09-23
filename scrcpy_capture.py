"""Capture the visible scrcpy video without starting another ADB screenshot.

The image is taken from the already-open scrcpy client area.  Capture refuses
to return a frame if that window is hidden, covered, unfocused, or moves during
the grab, because such a frame would be unsafe for touch planning.
"""

from __future__ import annotations

import ctypes

import cv2
import mss
import numpy as np

from scrcpy_touch import GA_ROOT, POINT, RECT, _windows_user32


class CaptureUnavailable(RuntimeError):
    """The visible scrcpy video cannot safely be captured."""


class ScrcpyCapture:
    """Read a BGR frame at the configured Android screenshot dimensions."""

    def __init__(
        self,
        window_title: str = "RMX3853",
        device_size: tuple[int, int] = (1080, 2376),
        *,
        user32=None,
        mss_factory=None,
    ):
        width, height = device_size
        if width <= 0 or height <= 0:
            raise ValueError("device_size must contain positive dimensions")
        self.device_size = (int(width), int(height))
        self.window_title = window_title
        self.user32 = user32 if user32 is not None else _windows_user32()
        self.mss_factory = mss_factory or mss.mss
        self._sct = None

    def _window(self):
        u = self.user32
        hwnd = u.FindWindowW(None, self.window_title)
        if not hwnd or not u.IsWindowVisible(hwnd) or u.IsIconic(hwnd):
            raise CaptureUnavailable(f"visible scrcpy window {self.window_title!r} unavailable")
        rect = RECT()
        origin = POINT()
        if not u.GetClientRect(hwnd, ctypes.byref(rect)) or not u.ClientToScreen(
            hwnd, ctypes.byref(origin)
        ):
            raise CaptureUnavailable("could not locate scrcpy client rectangle")
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width < 100 or height < 100:
            raise CaptureUnavailable("scrcpy client rectangle is too small")
        return hwnd, (origin.x, origin.y, width, height)

    def _video_rect(self, client):
        left, top, client_width, client_height = client
        device_width, device_height = self.device_size
        aspect = device_width / device_height
        if client_width / client_height > aspect:
            width = min(client_width, round(client_height * aspect))
            height = client_height
        else:
            width = client_width
            height = min(client_height, round(client_width / aspect))
        return (
            left + (client_width - width) // 2,
            top + (client_height - height) // 2,
            width,
            height,
        )

    def _validate(self, expected=None):
        hwnd, client = self._window()
        if expected is not None and (hwnd, client) != expected:
            raise CaptureUnavailable("scrcpy window moved or resized during capture")
        if self.user32.GetForegroundWindow() != hwnd:
            raise CaptureUnavailable("scrcpy window is not foreground")
        video = self._video_rect(client)
        left, top, width, height = video
        for fx in (0.02, 0.25, 0.5, 0.75, 0.98):
            for fy in (0.02, 0.25, 0.5, 0.75, 0.98):
                point = POINT(left + int((width - 1) * fx), top + int((height - 1) * fy))
                hovered = self.user32.WindowFromPoint(point)
                if not hovered or self.user32.GetAncestor(hovered, GA_ROOT) != hwnd:
                    raise CaptureUnavailable("scrcpy video is covered by another window")
        return hwnd, client, video

    def capture(self) -> np.ndarray:
        """Return the visible phone frame in OpenCV BGR order."""
        hwnd, client, video = self._validate()
        left, top, width, height = video
        if self._sct is None:
            try:
                self._sct = self.mss_factory()
            except Exception as exc:
                raise CaptureUnavailable(f"could not initialize screen capture: {exc}") from exc
        try:
            shot = self._sct.grab(
                {"left": left, "top": top, "width": width, "height": height}
            )
            pixels = np.asarray(shot)
        except Exception as exc:
            raise CaptureUnavailable(f"could not capture scrcpy video: {exc}") from exc
        self._validate((hwnd, client))
        if pixels.ndim != 3 or pixels.shape[:2] != (height, width) or pixels.shape[2] != 4:
            raise CaptureUnavailable("screen capture returned an unexpected image size")
        frame = cv2.cvtColor(pixels, cv2.COLOR_BGRA2BGR)
        return cv2.resize(frame, self.device_size, interpolation=cv2.INTER_LINEAR)

    def close(self) -> None:
        sct, self._sct = self._sct, None
        if sct is not None:
            sct.close()

