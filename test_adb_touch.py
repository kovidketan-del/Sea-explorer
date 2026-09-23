import unittest
from unittest.mock import Mock, patch

from sea_explorer_bot import ADBSwipeStream, ADBTouch


class ADBTouchTests(unittest.TestCase):
    @patch("sea_explorer_bot.ADBSwipeStream")
    def test_move_uses_persistent_swipe_stream(self, stream_class):
        adb=Mock()
        stream=stream_class.return_value
        touch=ADBTouch(adb)
        touch.begin(540,1608,1080,2376)
        touch.move_to(1026,1608,20,1080,2376)
        stream.swipe.assert_called_once_with(540,1608,1026,1608,20)
        self.assertTrue(touch.active)
        touch.release()
        stream.close.assert_called_once()
        self.assertFalse(touch.active)
        self.assertIsNone(touch.position)

    @patch("sea_explorer_bot.ADBSwipeStream")
    def test_interrupt_cancels_stream_without_losing_position(self, stream_class):
        touch=ADBTouch(Mock())
        touch.begin(540,1608,1080,2376)
        touch.interrupt()
        stream_class.return_value.interrupt.assert_called_once()
        self.assertEqual(touch.position,(540,1608))
        self.assertTrue(touch.active)


if __name__=="__main__":
    unittest.main()
