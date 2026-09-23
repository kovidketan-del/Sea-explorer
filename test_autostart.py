"""Offline checks for the desktop shortcut's connect-and-start behavior."""

import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import sea_explorer_ui as ui
from connection_manager import DeviceConnectionError


class AutoStartTests(unittest.TestCase):
    def _window(self, settings):
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        root.withdraw()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.addCleanup(root.destroy)
        self.enterContext(patch.object(ui, "_read_settings", return_value=settings))
        self.enterContext(patch.object(ui, "CHECKPOINT_PATH", Path(temporary.name) / "checkpoints.json"))
        self.enterContext(patch.object(ui, "LOG_PATH", Path(temporary.name) / "bot.log"))
        window = ui.SeaExplorerWindow(root)
        window._persist_settings = Mock()
        window._start_bot = Mock()
        errors = []

        def dispatch(work, success):
            try:
                success(work())
            except DeviceConnectionError as exc:
                errors.append(str(exc))

        window._dispatch = dispatch
        root.after = lambda _delay, callback: callback()
        return window, errors

    def test_saved_usb_serial_is_used_and_starts(self):
        window, errors = self._window({"mode": "usb", "usb_serial": "USB_A"})
        with patch.object(ui, "ConnectionManager") as manager_class:
            manager = manager_class.return_value
            manager.usb_devices.return_value = ["USB_A", "USB_B"]
            manager.connect_usb.return_value = "USB_A"
            window._autostart()
            manager.connect_usb.assert_called_once_with("USB_A")
        self.assertFalse(errors)
        window._start_bot.assert_called_once()

    def test_single_new_usb_device_starts_without_saved_serial(self):
        window, errors = self._window({"mode": "usb"})
        with patch.object(ui, "ConnectionManager") as manager_class:
            manager = manager_class.return_value
            manager.usb_devices.return_value = ["USB_ONLY"]
            manager.connect_usb.return_value = "USB_ONLY"
            window._autostart()
            manager.connect_usb.assert_called_once_with("USB_ONLY")
        self.assertFalse(errors)
        self.assertEqual(window.usb_serial.get(), "USB_ONLY")
        window._start_bot.assert_called_once()

    def test_no_usb_device_keeps_dashboard_open_without_start(self):
        window, errors = self._window({"mode": "usb"})
        with patch.object(ui, "ConnectionManager") as manager_class:
            manager_class.return_value.usb_devices.return_value = []
            window._autostart()
        self.assertTrue(errors)
        window._start_bot.assert_not_called()

    def test_wireless_reconnects_exact_saved_address_then_starts(self):
        address = "192.0.2.10:5555"
        window, errors = self._window({"mode": "wireless", "wireless_address": address})
        with patch.object(ui, "ConnectionManager") as manager_class:
            manager_class.return_value.connect_wireless.return_value = address
            window._autostart()
            manager_class.return_value.connect_wireless.assert_called_once_with(address)
        self.assertFalse(errors)
        window._start_bot.assert_called_once()


if __name__ == "__main__":
    unittest.main()
