import threading
import time
import unittest
from unittest.mock import Mock, patch

from sea_explorer_bot import ADB, ADBTouch, BotError, SeaExplorerBot, StopRequested, RunCompletionGate


class RunCompletionTests(unittest.TestCase):
    def test_result_animation_does_not_double_count_one_dive(self):
        gate=RunCompletionGate()
        states=(None,"PLAYING","TRANSITION","RESULT","PLAYING","WIN_MACHINE","HOME",
                "PLAYING","RESULT")
        counts=[gate.transition(a,b) for a,b in zip(states,states[1:])]
        self.assertEqual(sum(counts),2)
        self.assertFalse(counts[4])


class DeviceBindingTests(unittest.TestCase):
    def test_requested_serial_wins_with_multiple_authorized_devices(self):
        adb = ADB.__new__(ADB)
        adb.adb = "adb"
        adb.serial = None
        adb.devices = Mock(return_value=[
            ("USB123", "device"),
            ("192.0.2.10:5555", "device"),
            ("USB456", "unauthorized"),
        ])

        adb.ensure_ready("192.0.2.10:5555")

        self.assertEqual(adb.serial, "192.0.2.10:5555")
        self.assertEqual(adb._cmd("shell", "input", "keyevent", "BACK"), [
            adb.adb, "-s", "192.0.2.10:5555", "shell", "input", "keyevent", "BACK",
        ])

    def test_unavailable_or_unauthorized_requested_serial_is_rejected(self):
        adb = ADB.__new__(ADB)
        adb.serial = None
        adb.devices = Mock(return_value=[("USB123", "device"), ("USB456", "unauthorized")])

        for serial in ("USB456", "192.0.2.10:5555"):
            with self.subTest(serial=serial), self.assertRaises(BotError):
                adb.ensure_ready(serial)
            self.assertIsNone(adb.serial)

    @patch("sea_explorer_bot.TargetController")
    @patch("sea_explorer_bot.Progress.load")
    @patch("sea_explorer_bot.Vision")
    @patch("sea_explorer_bot.ADB")
    def test_bot_uses_direct_adb_touch_and_respects_wireless_transport(
        self, adb_class, _vision, _progress_load, sweeper_class
    ):
        adb = adb_class.return_value
        adb.serial = "192.0.2.10:5555"
        cfg = {"adb_path": "", "starting_oxygen_level": 230, "motion": {}}

        SeaExplorerBot(cfg, serial=adb.serial, transport="wireless")

        adb.ensure_ready.assert_called_once_with("192.0.2.10:5555")
        adb.keep_awake_usb.assert_not_called()
        touch=sweeper_class.call_args.args[0]
        self.assertIsInstance(touch, ADBTouch)
        self.assertIs(touch.adb, adb)
        self.assertEqual(sweeper_class.call_args.args[1], cfg)


class StopControlTests(unittest.TestCase):
    def test_sleep_wakes_when_stop_is_requested(self):
        stop = threading.Event()
        bot = SeaExplorerBot.__new__(SeaExplorerBot)
        bot.stop_event = stop
        timer = threading.Timer(0.02, stop.set)
        timer.start()
        started = time.monotonic()
        try:
            with self.assertRaises(StopRequested):
                bot._sleep(5)
        finally:
            timer.join(timeout=1)
        self.assertLess(time.monotonic() - started, 0.5)

    def test_stopped_bot_does_not_capture_or_tap(self):
        bot = SeaExplorerBot.__new__(SeaExplorerBot)
        bot.stop_event = threading.Event()
        bot.stop_event.set()
        bot.sweeper = Mock(active=False)
        bot.adb = Mock()
        bot.dry_run = False

        with self.assertRaises(StopRequested):
            bot._frame()
        with self.assertRaises(StopRequested):
            bot._tap_norm(Mock(), (0.5, 0.5))

        bot.adb.screenshot.assert_not_called()
        bot.adb.tap.assert_not_called()


if __name__ == "__main__":
    unittest.main()
