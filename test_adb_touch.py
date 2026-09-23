import unittest
from unittest.mock import Mock

from sea_explorer_bot import ADBTouch


class ADBTouchTests(unittest.TestCase):
    def test_move_uses_real_adb_swipe_between_control_points(self):
        adb = Mock()
        touch = ADBTouch(adb)
        touch.begin(540, 1608, 1080, 2376)
        touch.move_to(1026, 1608, 250, 1080, 2376)
        adb.swipe.assert_called_once_with(540, 1608, 1026, 1608, 250)
        self.assertTrue(touch.active)
        touch.release()
        self.assertFalse(touch.active)
        self.assertIsNone(touch.position)


if __name__ == "__main__":
    unittest.main()
