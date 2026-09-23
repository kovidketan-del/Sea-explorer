"""Offline checks for the desktop window's shutdown ordering."""

import tempfile
import threading
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

import sea_explorer_ui as ui


class RuntimeMotionSettingsTests(unittest.TestCase):
    def test_ui_motion_values_override_config(self):
        config={"motion":{"sweep_ms":110,"x_left":.05,"x_right":.95},"economy":{}}
        settings={
            "sweep_ms":140,
            "swipe_left_percent":20,
            "swipe_right_percent":80,
            "buy_upgrades":True,
            "upgrade_mode":"oxygen",
        }
        ui._apply_runtime_settings(config,settings)
        self.assertEqual(config["motion"]["sweep_ms"],140)
        self.assertEqual(config["motion"]["x_left"],.20)
        self.assertEqual(config["motion"]["x_right"],.80)
        self.assertTrue(config["economy"]["buy_upgrades"])
        self.assertEqual(config["economy"]["upgrade_mode"],"oxygen")

    def test_invalid_saved_values_fall_back_or_clamp(self):
        config={"motion":{"sweep_ms":110,"x_left":.05,"x_right":.95},"economy":{}}
        settings={"sweep_ms":"bad","swipe_left_percent":-50,"swipe_right_percent":150}
        ui._apply_runtime_settings(config,settings)
        self.assertEqual(config["motion"]["sweep_ms"],110)
        self.assertEqual(config["motion"]["x_left"],.02)
        self.assertEqual(config["motion"]["x_right"],.98)


class RecordingQueue:
    def __init__(self, events):
        self.events = events

    def put(self, item):
        self.events.append(("event", item[0]))


class DirectADBWorkerTests(unittest.TestCase):
    def test_bot_worker_runs_without_scrcpy(self):
        events = []
        window = ui.SeaExplorerWindow.__new__(ui.SeaExplorerWindow)
        window.events = RecordingQueue(events)
        stop_event = threading.Event()

        with patch.object(ui, "SeaExplorerBot") as bot:
            bot.return_value.run.side_effect = lambda: events.append(("bot", "run"))
            window._bot_worker({}, "USB123", "usb", stop_event)

        self.assertIn(("event", "bot_started"), events)
        self.assertIn(("bot", "run"), events)
        self.assertTrue(stop_event.is_set())
        self.assertEqual(events[-1], ("event", "bot_done"))

    def test_stop_request_is_clean(self):
        events = []
        window = ui.SeaExplorerWindow.__new__(ui.SeaExplorerWindow)
        window.events = RecordingQueue(events)
        stop_event = threading.Event()

        with patch.object(ui, "SeaExplorerBot") as bot:
            bot.return_value.run.side_effect = ui.StopRequested("stop")
            window._bot_worker({}, "USB123", "usb", stop_event)

        self.assertTrue(stop_event.is_set())
        self.assertEqual(events[-1], ("event", "bot_done"))


class ConnectionCloseTests(unittest.TestCase):
    def test_close_waits_for_in_flight_connection_worker(self):
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        root.withdraw()

        def window_exists():
            try:
                return bool(root.winfo_exists())
            except tk.TclError:
                return False

        started = threading.Event()
        release = threading.Event()
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(ui, "_read_settings", return_value={}), \
             patch.object(ui, "CHECKPOINT_PATH", Path(temporary) / "checkpoints.json"), \
             patch.object(ui, "LOG_PATH", Path(temporary) / "bot.log"):
            try:
                window = ui.SeaExplorerWindow(root)

                def connect():
                    started.set()
                    release.wait(2)
                    return "USB123"

                window._dispatch(connect, lambda serial: window._connected("usb", serial))
                self.assertTrue(started.wait(1), "connection worker did not start")
                self.assertTrue(window.busy)

                window._on_close()
                window._periodic()
                self.assertTrue(window.closing)
                self.assertTrue(window_exists(), "window closed while ADB work was active")

                release.set()
                deadline = time.monotonic() + 2
                while window.events.empty() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertFalse(window.events.empty(), "connection result was not queued")
                window._periodic()
                self.assertFalse(window_exists(), "window stayed open after worker finished")
            finally:
                release.set()
                if window_exists():
                    root.destroy()


if __name__ == "__main__":
    unittest.main()
