import ctypes
import unittest

from scrcpy_touch import (
    GA_ROOT,
    MOUSEEVENTF_LEFTDOWN,
    MOUSEEVENTF_LEFTUP,
    INPUT,
    POINT,
    RECT,
    ScrcpyTouch,
    TouchUnavailable,
)


class FakeUser32:
    def __init__(self):
        self.hwnd = 101
        self.foreground = 202
        self.focus_delay_reads = 0
        self.pending_focus = None
        self.visible = True
        self.iconic = False
        self.covered = False
        self.origin = (1400, 50)
        self.size = (420, 924)
        self.cursor = (90, 90)
        self.actions = []
        self.move_count = 0
        self.cover_after_moves = None

    def FindWindowW(self, cls, title):
        return self.hwnd if title == "RMX3853" else 0

    def IsWindowVisible(self, hwnd):
        return self.visible

    def IsIconic(self, hwnd):
        return self.iconic

    def GetClientRect(self, hwnd, ptr):
        rect = ctypes.cast(ptr, ctypes.POINTER(RECT)).contents
        rect.left, rect.top = 0, 0
        rect.right, rect.bottom = self.size
        return True

    def ClientToScreen(self, hwnd, ptr):
        point = ctypes.cast(ptr, ctypes.POINTER(POINT)).contents
        point.x, point.y = self.origin
        return True

    def SetForegroundWindow(self, hwnd):
        if self.focus_delay_reads:
            self.pending_focus = hwnd
        else:
            self.foreground = hwnd
        return True

    def GetForegroundWindow(self):
        if self.pending_focus is not None:
            self.focus_delay_reads -= 1
            if self.focus_delay_reads <= 0:
                self.foreground = self.pending_focus
                self.pending_focus = None
        return self.foreground

    def WindowFromPoint(self, point):
        x, y = point.x, point.y
        left, top = self.origin
        width, height = self.size
        if self.covered or not (left <= x < left + width and top <= y < top + height):
            return 202
        return self.hwnd

    def GetAncestor(self, hwnd, flag):
        assert flag == GA_ROOT
        return hwnd

    def GetCursorPos(self, ptr):
        point = ctypes.cast(ptr, ctypes.POINTER(POINT)).contents
        point.x, point.y = self.cursor
        return True

    def SetCursorPos(self, x, y):
        self.cursor = (x, y)
        self.actions.append(("move", x, y))
        self.move_count += 1
        if self.cover_after_moves == self.move_count:
            self.covered = True
        return True

    def SendInput(self, count, ptr, size):
        assert count == 1
        assert size == ctypes.sizeof(INPUT)
        flag = ctypes.cast(ptr, ctypes.POINTER(INPUT)).contents.u.mi.dwFlags
        self.actions.append(("button", flag))
        return 1


class ScrcpyTouchTests(unittest.TestCase):
    def setUp(self):
        self.user32 = FakeUser32()
        self.now = 0.0

        def sleep(seconds):
            self.now += seconds

        self.touch = ScrcpyTouch(
            user32=self.user32,
            clock=lambda: self.now,
            sleep=sleep,
        )

    def test_one_press_is_held_across_two_paced_passes(self):
        self.touch.begin(540, 1188, 1080, 2376)
        self.assertEqual(self.user32.cursor, (1610, 512))
        self.touch.move_to(1026, 1188, 182, 1080, 2376)
        self.touch.move_to(54, 1188, 182, 1080, 2376)
        flags = [event[1] for event in self.user32.actions if event[0] == "button"]
        self.assertEqual(flags, [MOUSEEVENTF_LEFTDOWN])
        self.assertTrue(self.touch.active)
        self.assertGreaterEqual(self.user32.move_count, 25)
        self.assertAlmostEqual(self.now, 0.364)
        self.touch.release()
        flags = [event[1] for event in self.user32.actions if event[0] == "button"]
        self.assertEqual(flags, [MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP])
        self.assertEqual(self.user32.cursor, (90, 90))

    def test_missing_window_never_moves_cursor_or_sends_button(self):
        self.user32.hwnd = 0
        with self.assertRaises(TouchUnavailable):
            self.touch.begin(540, 1188, 1080, 2376)
        self.assertEqual(self.user32.actions, [])

    def test_delayed_window_focus_is_allowed_to_settle(self):
        self.user32.focus_delay_reads = 4
        self.touch.begin(540, 1188, 1080, 2376)
        self.assertTrue(self.touch.active)
        self.assertEqual(self.user32.foreground, self.user32.hwnd)
        self.assertGreater(self.now, 0)
        self.assertLess(self.now, 0.5)
        self.touch.release()

    def test_focus_timeout_never_moves_cursor_or_sends_button(self):
        self.user32.focus_delay_reads = 1000
        with self.assertRaisesRegex(TouchUnavailable, "not focused"):
            self.touch.begin(540, 1188, 1080, 2376)
        self.assertAlmostEqual(self.now, 0.5)
        self.assertEqual(self.user32.actions, [])

    def test_occluded_route_releases_before_moving_into_it(self):
        self.touch.begin(540, 1188, 1080, 2376)
        self.user32.covered = True
        with self.assertRaisesRegex(TouchUnavailable, "covered"):
            self.touch.move_to(1026, 1188, 182, 1080, 2376)
        self.assertFalse(self.touch.active)
        self.assertEqual(self.user32.actions[-2], ("button", MOUSEEVENTF_LEFTUP))

    def test_window_resize_releases_held_button(self):
        self.touch.begin(540, 1188, 1080, 2376)
        self.user32.size = (430, 924)
        with self.assertRaisesRegex(TouchUnavailable, "resized"):
            self.touch.move_to(1026, 1188, 182, 1080, 2376)
        self.assertFalse(self.touch.active)
        self.assertIn(("button", MOUSEEVENTF_LEFTUP), self.user32.actions)

    def test_user_cursor_movement_releases_without_overriding_it(self):
        self.touch.begin(540, 1188, 1080, 2376)
        self.user32.cursor = (100, 100)
        with self.assertRaisesRegex(TouchUnavailable, "outside bot control"):
            self.touch.move_to(1026, 1188, 182, 1080, 2376)
        self.assertEqual(self.user32.cursor, (100, 100))
        self.assertFalse(self.touch.active)
        self.assertIn(("button", MOUSEEVENTF_LEFTUP), self.user32.actions)


if __name__ == "__main__":
    unittest.main()
