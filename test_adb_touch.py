import unittest
from unittest.mock import Mock

from sea_explorer_bot import ADBTouch


class ADBTouchTests(unittest.TestCase):
    def test_down_moves_and_up_keep_one_pointer_held(self):
        adb = Mock()
        touch = ADBTouch(adb)
        touch.begin(540, 1608, 1080, 2376)
        touch.move_to(1026, 1608, 250, 1080, 2376)
        self.assertTrue(touch.active)
        touch.release()
        self.assertEqual(adb.run.call_args_list, [
            unittest.mock.call("shell", "input", "touchscreen", "motionevent", "DOWN", 540, 1608, timeout=3),
            unittest.mock.call("shell", "input", "touchscreen", "motionevent", "MOVE", 1026, 1608, timeout=3),
            unittest.mock.call("shell", "input", "touchscreen", "motionevent", "UP", 1026, 1608, timeout=3),
        ])
        adb.swipe.assert_not_called()
        self.assertFalse(touch.active)
        self.assertIsNone(touch.position)


if __name__ == "__main__":
    unittest.main()
