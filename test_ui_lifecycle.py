"""Offline checks for the desktop window's shutdown ordering."""

import tempfile
import threading
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

import sea_explorer_ui as ui


class RecordingQueue:
    def __init__(self, events):
        self.events = events

    def put(self, item):
        self.events.append(("event", item[0]))


class FakeMirrorProcess:
    def __init__(self, events, *, timeout_on_first_wait=False):
        self.events = events
        self.running = True
        self.timeout_on_first_wait = timeout_on_first_wait
        self.wait_count = 0

    def poll(self):
        return None if self.running else 0

    def terminate(self):
        self.events.append(("mirror", "terminate"))
        if not self.timeout_on_first_wait:
            self.running = False

    def kill(self):
        self.events.append(("mirror", "kill"))
        self.running = False

    def wait(self, timeout):
        self.wait_count += 1
        self.events.append(("mirror", "wait"))
        if self.timeout_on_first_wait and self.wait_count == 1:
            raise ui.subprocess.TimeoutExpired("scrcpy", timeout)
        return 0


class MirrorShutdownTests(unittest.TestCase):
    def _run_worker(self, *, first_wait_times_out=False):
        events = []
        proc = FakeMirrorProcess(events, timeout_on_first_wait=first_wait_times_out)
        window = ui.SeaExplorerWindow.__new__(ui.SeaExplorerWindow)
        window.events = RecordingQueue(events)
        stop_event = threading.Event()

        with patch.object(ui.subprocess, "Popen", return_value=proc) as popen, \
             patch.object(ui, "ScrcpyTouch") as touch, \
             patch.object(ui, "SeaExplorerBot") as bot:
            bot.return_value.run.side_effect = lambda: events.append(("bot", "run"))
            touch.return_value._window.return_value = object()
            window._bot_worker({}, "USB123", "usb", "Test Mirror", "scrcpy.exe",
                               (0, 0, 300, 700), stop_event)

        popen.assert_called_once()
        self.assertIn(("event", "bot_started"), events)
        self.assertIn(("bot", "run"), events)
        self.assertTrue(stop_event.is_set())
        self.assertEqual(events[-1], ("event", "bot_done"))
        self.assertLess(events.index(("mirror", "wait")), events.index(("event", "bot_done")))
        return events

    def test_bot_done_follows_owned_mirror_terminate_and_wait(self):
        events = self._run_worker()
        self.assertLess(events.index(("mirror", "terminate")),
                        events.index(("mirror", "wait")))

    def test_bot_done_follows_kill_and_second_wait_after_timeout(self):
        events = self._run_worker(first_wait_times_out=True)
        self.assertEqual(events.count(("mirror", "wait")), 2)
        self.assertEqual(events[-3:], [
            ("mirror", "kill"), ("mirror", "wait"), ("event", "bot_done"),
        ])


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
