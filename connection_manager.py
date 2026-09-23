"""Select an exact USB or wireless ADB device for the Sea Explorer bot.

This module only manages the ADB connection. It does not start the bot, scrcpy,
or a game. Pairing codes are passed to ADB once and are never logged or saved.
"""

from __future__ import annotations

import ipaddress
import re
import subprocess


class DeviceConnectionError(RuntimeError):
    """An ADB device could not be selected or connected."""


_HOST_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z")
_SIX_DIGITS = re.compile(r"[0-9]{6}\Z")


def _address(value: str) -> str:
    """Return a validated ADB host:port without resolving or contacting it."""
    if not isinstance(value, str):
        raise DeviceConnectionError("Enter a wireless address as host:port.")
    value = value.strip()
    if value.startswith("["):
        match = re.fullmatch(r"\[([^\]]+)\]:([0-9]{1,5})", value)
        if match is None:
            raise DeviceConnectionError("Enter an IPv6 address as [address]:port.")
        host, port_text = match.groups()
        try:
            ipaddress.IPv6Address(host)
        except ValueError:
            raise DeviceConnectionError("Invalid IPv6 address.") from None
        prefix = f"[{host}]"
    else:
        match = re.fullmatch(r"([^:\s]+):([0-9]{1,5})", value)
        if match is None:
            raise DeviceConnectionError("Enter a wireless address as host:port.")
        host, port_text = match.groups()
        try:
            ipaddress.IPv4Address(host)
        except ValueError:
            if re.fullmatch(r"[0-9.]+", host):
                raise DeviceConnectionError("Invalid IPv4 address.") from None
            labels = host.split(".")
            if len(host) > 253 or any(_HOST_LABEL.fullmatch(label) is None for label in labels):
                raise DeviceConnectionError("Invalid wireless hostname.") from None
        prefix = host
    port = int(port_text)
    if not 1 <= port <= 65535:
        raise DeviceConnectionError("Wireless port must be between 1 and 65535.")
    return f"{prefix}:{port}"


class ConnectionManager:
    """Use one ADB executable and require an exact authorized target serial."""

    def __init__(self, adb_path: str):
        if not adb_path:
            raise DeviceConnectionError("ADB executable path is required.")
        self.adb_path = str(adb_path)

    def _run(self, *args: str, timeout: float = 10) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                [self.adb_path, *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            # Do not include command arguments: a pairing code may be among them.
            raise DeviceConnectionError("ADB timed out.") from None
        except OSError:
            raise DeviceConnectionError("Could not run ADB. Check its executable path.") from None

    def devices(self) -> list[tuple[str, str]]:
        """Return (serial, state), including unauthorized and offline devices."""
        result = self._run("devices")
        if result.returncode != 0:
            raise DeviceConnectionError("Could not list ADB devices.")
        devices = []
        for line in result.stdout.splitlines():
            if "\t" not in line:
                continue
            serial, state = line.split("\t", 1)
            state = state.strip().split(maxsplit=1)
            if serial.strip() and state:
                devices.append((serial.strip(), state[0]))
        return devices

    def usb_devices(self) -> list[str]:
        """List authorized physical USB serials, excluding network and emulators."""
        return [
            serial
            for serial, state in self.devices()
            if state == "device" and ":" not in serial and not serial.startswith("emulator-")
        ]

    def connect_usb(self, serial: str) -> str:
        """Verify that the chosen USB serial is present and authorized."""
        serial = serial.strip() if isinstance(serial, str) else ""
        if not serial or ":" in serial or serial.startswith("emulator-"):
            raise DeviceConnectionError("Select a USB device serial.")
        states = dict(self.devices())
        state = states.get(serial)
        if state is None:
            raise DeviceConnectionError(f"USB device {serial!r} is not connected.")
        if state != "device":
            raise DeviceConnectionError(f"USB device {serial!r} is {state}; authorize it on the phone.")
        return serial

    def pair_wireless(self, pair_address: str, six_digit_code: str) -> None:
        """Pair with Android Wireless debugging; never retain the pairing code."""
        address = _address(pair_address)
        if not isinstance(six_digit_code, str) or _SIX_DIGITS.fullmatch(six_digit_code) is None:
            raise DeviceConnectionError("Pairing code must contain six digits.")
        result = self._run("pair", address, six_digit_code, timeout=30)
        output = f"{result.stdout}\n{result.stderr}".lower()
        if result.returncode != 0 or "failed" in output or "unable" in output:
            raise DeviceConnectionError("Wireless pairing failed. Check the address and pairing code.")

    def connect_wireless(self, connect_address: str) -> str:
        """Connect and verify the exact authorized wireless device serial."""
        address = _address(connect_address)
        result = self._run("connect", address, timeout=20)
        if result.returncode != 0:
            raise DeviceConnectionError(f"Could not connect to wireless device {address}.")
        state = dict(self.devices()).get(address)
        if state is None:
            raise DeviceConnectionError(f"Wireless device {address} did not appear in ADB devices.")
        if state != "device":
            raise DeviceConnectionError(f"Wireless device {address} is {state}; authorize it on the phone.")
        return address
