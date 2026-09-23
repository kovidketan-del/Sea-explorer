import unittest

import numpy as np

from scrcpy_capture import CaptureUnavailable, ScrcpyCapture
from test_scrcpy_touch import FakeUser32


class FakeMss:
    def __init__(self, user32):
        self.user32 = user32
        self.regions = []
        self.move_window_during_grab = False
        self.closed = False

    def grab(self, region):
        self.regions.append(region)
        if self.move_window_during_grab:
            self.user32.origin = (self.user32.origin[0] + 10, self.user32.origin[1])
        pixels = np.zeros((region["height"], region["width"], 4), dtype=np.uint8)
        pixels[:, :, 0] = 10  # blue
        pixels[:, :, 1] = 20  # green
        pixels[:, :, 2] = 30  # red
        pixels[:, :, 3] = 255
        return pixels

    def close(self):
        self.closed = True


class ScrcpyCaptureTests(unittest.TestCase):
    def setUp(self):
        self.user32 = FakeUser32()
        self.user32.foreground = self.user32.hwnd
        self.user32.size = (120, 200)
        self.sct = FakeMss(self.user32)
        self.capture = ScrcpyCapture(
            device_size=(100, 200), user32=self.user32, mss_factory=lambda: self.sct
        )

    def test_crops_side_letterbox_and_returns_bgr(self):
        frame = self.capture.capture()
        self.assertEqual(frame.shape, (200, 100, 3))
        self.assertTrue(np.all(frame == (10, 20, 30)))
        self.assertEqual(
            self.sct.regions,
            [{"left": 1410, "top": 50, "width": 100, "height": 200}],
        )
        self.capture.close()
        self.assertTrue(self.sct.closed)

    def test_crops_top_and_bottom_letterbox(self):
        self.user32.size = (100, 240)
        frame = self.capture.capture()
        self.assertEqual(frame.shape, (200, 100, 3))
        self.assertEqual(
            self.sct.regions,
            [{"left": 1400, "top": 70, "width": 100, "height": 200}],
        )

    def test_covered_window_is_rejected_before_capture(self):
        self.user32.covered = True
        with self.assertRaisesRegex(CaptureUnavailable, "covered"):
            self.capture.capture()
        self.assertEqual(self.sct.regions, [])

    def test_unfocused_window_is_rejected_before_capture(self):
        self.user32.foreground = 202
        with self.assertRaisesRegex(CaptureUnavailable, "foreground"):
            self.capture.capture()
        self.assertEqual(self.sct.regions, [])

    def test_minimized_window_is_rejected_before_capture(self):
        self.user32.iconic = True
        with self.assertRaisesRegex(CaptureUnavailable, "unavailable"):
            self.capture.capture()
        self.assertEqual(self.sct.regions, [])

    def test_window_move_during_grab_discards_frame(self):
        self.sct.move_window_during_grab = True
        with self.assertRaisesRegex(CaptureUnavailable, "moved"):
            self.capture.capture()
        self.assertEqual(len(self.sct.regions), 1)


if __name__ == "__main__":
    unittest.main()
