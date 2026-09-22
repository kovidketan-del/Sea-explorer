from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parent
BUILD = "one-go-livefix-1"
CONFIG_PATH = ROOT / "config.json"
STATE_PATH = ROOT / "progress_state.json"
LOG_PATH = ROOT / "sea_explorer.log"


class BotError(RuntimeError):
    pass


def log(msg: str) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {msg}"
    print(line, flush=True)
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def load_config() -> dict:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


class ADB:
    def __init__(self, configured_path: str = ""):
        self.adb = self._find_adb(configured_path)
        self.serial = None
        self._motionevent = None

    @staticmethod
    def _find_adb(configured_path: str) -> str:
        candidates = []
        if configured_path:
            candidates.append(Path(os.path.expandvars(configured_path)))
        if os.environ.get("ADB"):
            candidates.append(Path(os.path.expandvars(os.environ["ADB"])))
        p = shutil.which("adb")
        if p:
            candidates.append(Path(p))

        local = os.environ.get("LOCALAPPDATA")
        if local:
            candidates.append(Path(local) / "Android" / "Sdk" / "platform-tools" / "adb.exe")

        # Useful when this bot is placed beside/inside the earlier Hole Collect project.
        for base in [Path.cwd(), ROOT, ROOT.parent, Path.home() / "platform-tools"]:
            candidates.extend([
                base / "platform-tools" / ("adb.exe" if os.name == "nt" else "adb"),
                base / "adb" / ("adb.exe" if os.name == "nt" else "adb"),
            ])

        seen = set()
        for raw in candidates:
            p = raw
            if p.is_dir():
                p = p / ("adb.exe" if os.name == "nt" else "adb")
            key = str(p.resolve()) if p.exists() else str(p)
            if key in seen:
                continue
            seen.add(key)
            if not p.is_file():
                continue
            try:
                r = subprocess.run([str(p), "version"], capture_output=True, text=True, timeout=5)
                if r.returncode == 0:
                    return str(p)
            except Exception:
                pass
        raise BotError("ADB not found. Put adb.exe on PATH, set the ADB environment variable, or set adb_path in config.json.")

    def _cmd(self, *args):
        cmd = [self.adb]
        if self.serial:
            cmd += ["-s", self.serial]
        cmd += [str(x) for x in args]
        return cmd

    def run(self, *args, text=True, check=True, timeout=20):
        try:
            r = subprocess.run(self._cmd(*args), capture_output=True, text=text, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            raise BotError(f"ADB timeout: {' '.join(map(str,args))}") from e
        if check and r.returncode != 0:
            err = r.stderr if text else r.stderr.decode(errors="replace")
            raise BotError(err.strip() or f"ADB command failed: {' '.join(map(str,args))}")
        return r.stdout

    def devices(self):
        out = self.run("devices")
        result = []
        for line in out.splitlines()[1:]:
            if "\t" in line:
                a, b = line.split("\t", 1)
                result.append((a.strip(), b.strip()))
        return result

    def ensure_ready(self):
        ready = [s for s, st in self.devices() if st == "device"]
        if not ready:
            raise BotError("No authorized Android device. Connect USB, enable USB debugging, unlock the phone, and accept the RSA prompt.")
        if len(ready) > 1:
            raise BotError(f"Multiple devices connected: {ready}. Disconnect all but the test phone.")
        self.serial = ready[0]

    def screenshot(self):
        raw = self.run("exec-out", "screencap", "-p", text=False, timeout=15)
        arr = np.frombuffer(raw, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            raise BotError("Could not decode Android screenshot.")
        return frame

    def tap(self, x, y):
        self.run("shell", "input", "tap", int(x), int(y))

    def back(self):
        self.run("shell", "input", "keyevent", "BACK")

    def motionevent(self, action, x, y, check=True):
        return self.run("shell", "input", "motionevent", action.upper(), int(x), int(y), check=check)

    def swipe(self, x1, y1, x2, y2, ms=90):
        self.run("shell", "input", "swipe", int(x1), int(y1), int(x2), int(y2), int(ms))

    def supports_motionevent(self):
        if self._motionevent is not None:
            return self._motionevent
        try:
            r1 = subprocess.run(self._cmd("shell","input","motionevent","DOWN","1","1"), capture_output=True, text=True, timeout=5)
            r2 = subprocess.run(self._cmd("shell","input","motionevent","UP","1","1"), capture_output=True, text=True, timeout=5)
            bad = (r1.stdout+r1.stderr+r2.stdout+r2.stderr).lower()
            self._motionevent = r1.returncode == 0 and r2.returncode == 0 and "unknown" not in bad and "usage:" not in bad
        except Exception:
            self._motionevent = False
        return self._motionevent

    def foreground_package(self):
        for args in [
            ("shell","dumpsys","window","windows"),
            ("shell","dumpsys","activity","activities"),
        ]:
            try:
                out = self.run(*args, check=False)
            except Exception:
                continue
            patterns = [
                r"mCurrentFocus=.*? ([A-Za-z0-9._]+)/",
                r"mFocusedApp=.*? ([A-Za-z0-9._]+)/",
                r"topResumedActivity=.*? ([A-Za-z0-9._]+)/",
                r"ResumedActivity:.*? ([A-Za-z0-9._]+)/",
            ]
            for pat in patterns:
                m = re.search(pat, out)
                if m:
                    return m.group(1)
        return None

    def start_app(self, package):
        self.run("shell","monkey","-p",package,"-c","android.intent.category.LAUNCHER","1", check=False)

    def keep_awake_usb(self):
        self.run("shell","svc","power","stayon","usb",check=False)


@dataclass
class Progress:
    package: str = ""
    oxygen_level_estimate: int = 170
    oxygen_purchases: int = 0
    bag_purchases: int = 0
    runs_completed: int = 0
    last_bag_attempt_run: int = -999

    @classmethod
