import subprocess
import unittest
from unittest.mock import patch

from connection_manager import ConnectionManager, DeviceConnectionError


ADB = r"C:\Android\platform-tools\adb.exe"


def completed(*args, stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(args, returncode, stdout, stderr)


class ConnectionManagerTests(unittest.TestCase):
    def setUp(self):
        self.manager = ConnectionManager(ADB)

    @patch("connection_manager.subprocess.run")
    def test_lists_device_states_and_only_ready_usb_serials(self, run):
        run.return_value = completed(
            "devices",
            stdout=(
                "List of devices attached\n"
                "RMX3853\tdevice\n"
                "192.168.1.7:37123\tdevice\n"
                "pending\tunauthorized\n"
                "emulator-5554\tdevice\n"
            ),
        )
        self.assertEqual(self.manager.devices(), [
            ("RMX3853", "device"),
            ("192.168.1.7:37123", "device"),
            ("pending", "unauthorized"),
            ("emulator-5554", "device"),
        ])
        self.assertEqual(self.manager.usb_devices(), ["RMX3853"])
        self.assertEqual(run.call_args.args[0], [ADB, "devices"])
        self.assertTrue(run.call_args.kwargs["capture_output"])
        self.assertEqual(run.call_args.kwargs["timeout"], 10)

    @patch("connection_manager.subprocess.run")
    def test_usb_selects_exact_serial_and_rejects_unauthorized(self, run):
        run.return_value = completed(
            "devices",
            stdout="List of devices attached\nphone-a\tdevice\nphone-b\tunauthorized\n",
        )
        self.assertEqual(self.manager.connect_usb("phone-a"), "phone-a")
        with self.assertRaisesRegex(DeviceConnectionError, "unauthorized"):
            self.manager.connect_usb("phone-b")
        with self.assertRaisesRegex(DeviceConnectionError, "not connected"):
            self.manager.connect_usb("phone-c")
        with self.assertRaisesRegex(DeviceConnectionError, "USB"):
            self.manager.connect_usb("192.168.1.7:37123")

    @patch("connection_manager.subprocess.run")
    def test_pairing_uses_argument_list_and_does_not_save_or_report_code(self, run):
        run.return_value = completed("pair", stdout="Successfully paired")
        self.manager.pair_wireless("192.168.1.7:38271", "123456")
        run.assert_called_once_with(
            [ADB, "pair", "192.168.1.7:38271", "123456"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        self.assertNotIn("123456", vars(self.manager).values())
        run.return_value = completed("pair", returncode=1, stderr="pairing failed")
        with self.assertRaises(DeviceConnectionError) as caught:
            self.manager.pair_wireless("192.168.1.7:38271", "123456")
        self.assertNotIn("123456", str(caught.exception))

    @patch("connection_manager.subprocess.run")
    def test_wireless_connect_verifies_exact_ready_serial(self, run):
        run.side_effect = [
            completed("connect", stdout="connected to dive-phone.local:37123"),
            completed("devices", stdout=(
                "List of devices attached\n"
                "usb-phone\tdevice\n"
                "dive-phone.local:37123\tdevice\n"
            )),
        ]
        self.assertEqual(
            self.manager.connect_wireless("dive-phone.local:37123"),
            "dive-phone.local:37123",
        )
        self.assertEqual(run.call_args_list[0].args[0], [ADB, "connect", "dive-phone.local:37123"])
        self.assertEqual(run.call_args_list[1].args[0], [ADB, "devices"])

    @patch("connection_manager.subprocess.run")
    def test_wireless_rejects_other_device_and_offline_target(self, run):
        run.side_effect = [
            completed("connect"),
            completed("devices", stdout="List of devices attached\nother:37123\tdevice\n"),
        ]
        with self.assertRaisesRegex(DeviceConnectionError, "did not appear"):
            self.manager.connect_wireless("dive-phone.local:37123")
        run.side_effect = [
            completed("connect"),
            completed("devices", stdout="List of devices attached\ndive-phone.local:37123\toffline\n"),
        ]
        with self.assertRaisesRegex(DeviceConnectionError, "offline"):
            self.manager.connect_wireless("dive-phone.local:37123")

    @patch("connection_manager.subprocess.run")
    def test_rejects_invalid_addresses_and_codes_without_running_adb(self, run):
        for address in ("", "192.168.1.2", "999.1.1.1:5555", "host:0", "host:65536", "host;bad:5555", "http://host:5555"):
            with self.subTest(address=address), self.assertRaises(DeviceConnectionError):
                self.manager.connect_wireless(address)
        with self.assertRaises(DeviceConnectionError):
            self.manager.pair_wireless("host:5555", "12 456")
        run.assert_not_called()

    @patch("connection_manager.subprocess.run")
    def test_pairing_timeout_does_not_expose_code(self, run):
        run.side_effect = subprocess.TimeoutExpired([ADB, "pair", "host:5555", "123456"], 30)
        with self.assertRaises(DeviceConnectionError) as caught:
            self.manager.pair_wireless("host:5555", "123456")
        self.assertNotIn("123456", str(caught.exception))

    @patch("connection_manager.subprocess.run")
    def test_bracketed_ipv6_address(self, run):
        run.side_effect = [
            completed("connect"),
            completed("devices", stdout="List of devices attached\n[::1]:5555\tdevice\n"),
        ]
        self.assertEqual(self.manager.connect_wireless("[::1]:5555"), "[::1]:5555")


if __name__ == "__main__":
    unittest.main()
