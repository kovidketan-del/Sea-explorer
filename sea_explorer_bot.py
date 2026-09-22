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
    def load(cls, starting_oxygen: int):
        if STATE_PATH.exists():
            try:
                data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
                return cls(**{k: data[k] for k in cls.__annotations__ if k in data})
            except Exception:
                pass
        return cls(oxygen_level_estimate=starting_oxygen)

    def save(self):
        tmp = STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        tmp.replace(STATE_PATH)


@dataclass
class Detection:
    state: str
    confidence: float
    orange_button: tuple[int,int] | None = None
    popup_close: tuple[int,int] | None = None
    hazards: tuple[tuple[int,int,int], ...] = ()


class Vision:
    def __init__(self, cfg):
        self.cfg = cfg

    @staticmethod
    def _hsv(frame):
        return cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

    @staticmethod
    def _components(mask, min_area=20):
        n, _, stats, cents = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
        out = []
        for i in range(1, n):
            x,y,w,h,area = map(int, stats[i])
            if area < min_area or w <= 0 or h <= 0:
                continue
            out.append((x,y,w,h,area,float(cents[i][0]),float(cents[i][1])))
        return out

    @staticmethod
    def _color_mask(hsv, h_lo, h_hi, s_min, v_min):
        H,S,V = cv2.split(hsv)
        if h_lo <= h_hi:
            m = (H >= h_lo) & (H <= h_hi)
        else:
            m = (H >= h_lo) | (H <= h_hi)
        return (m & (S >= s_min) & (V >= v_min)).astype(np.uint8)

    def _green_buttons(self, hsv):
        h,w = hsv.shape[:2]
        mask = self._color_mask(hsv, 35, 95, 80, 70)
        comps = self._components(mask, max(60, int(h*w*0.00008)))
        good=[]
        for x,y,bw,bh,a,cx,cy in comps:
            wr,hr=bw/w,bh/h
            fill=a/max(1,bw*bh)
            if 0.08 <= wr <= 0.45 and 0.018 <= hr <= 0.09 and fill >= 0.35:
                good.append((x,y,bw,bh,a,cx,cy))
        return good

    def _orange_components(self, hsv):
        h,w=hsv.shape[:2]
        mask=self._color_mask(hsv, 3, 28, 100, 80)
        return self._components(mask,max(80,int(h*w*0.0001)))

    def _gameplay_hud(self, hsv):
        h,w=hsv.shape[:2]
        comps=self._orange_components(hsv)
        left=right=False
        for x,y,bw,bh,a,cx,cy in comps:
            wr,hr=bw/w,bh/h
            cyr=cy/h
            if not (0.045 <= cyr <= 0.16 and 0.10 <= wr <= 0.33 and 0.025 <= hr <= 0.09):
                continue
            if cx/w < .38: left=True
            if cx/w > .62: right=True
        return left and right

    def _home(self, hsv):
        h,w=hsv.shape[:2]
        if self._gameplay_hud(hsv):
            return False

        # Do NOT use the upper SHOP button as the primary HOME cue.
        # On this game the turquoise/cyan HOME background falls inside the
        # same HSV range as the SHOP button and can merge it into one huge
        # connected component. That is exactly what happened on the live
        # 720x1584 phone frame.
        #
        # The two lower green purchase buttons (BAG SIZE and OXYGEN LEVEL)
        # are much more isolated and remain distinct across the supplied
        # recordings. Require both, on the same lower row.
        greens=self._green_buttons(hsv)
        left_upgrade=False
        middle_upgrade=False

        for x,y,bw,bh,a,cx,cy in greens:
            xr,yr=cx/w,cy/h
            wr,hr=bw/w,bh/h
            if not (0.12 <= wr <= 0.27 and 0.022 <= hr <= 0.065 and 0.73 <= yr <= 0.88):
                continue
            if 0.12 <= xr <= 0.34:
                left_upgrade=True
            elif 0.36 <= xr <= 0.62:
                middle_upgrade=True

        return left_upgrade and middle_upgrade

    def _large_blue_popup(self, hsv):
        h,w=hsv.shape[:2]
        mask=self._color_mask(hsv, 85, 125, 80, 80)
        comps=self._components(mask,max(100,int(h*w*.001)))
        for x,y,bw,bh,a,cx,cy in comps:
            wr,hr=bw/w,bh/h
            if .48 <= wr <= .92 and .11 <= hr <= .40 and .30 <= cx/w <= .70 and .35 <= cy/h <= .72:
                greens=self._green_buttons(hsv)
                close=None
                for gx,gy,gw,gh,ga,gcx,gcy in greens:
                    if .34 <= gcx/w <= .66 and .46 <= gcy/h <= .76:
                        close=(int(gcx),int(gcy))
                return True, close
        return False, None

    def _win_machine(self, hsv):
        h,w=hsv.shape[:2]
        mask=self._color_mask(hsv, 125, 175, 80, 60)
        roi=mask[int(.12*h):int(.78*h),:]
        frac=float(np.mean(roi)) if roi.size else 0.0
        # The slot-machine popup is an enormous purple object occupying the center.
        return frac >= 0.10

    def _lower_orange_button(self, hsv):
        h,w=hsv.shape[:2]
        best=None
        for x,y,bw,bh,a,cx,cy in self._orange_components(hsv):
            wr,hr=bw/w,bh/h
            xr,yr=cx/w,cy/h
            if .18 <= wr <= .52 and .025 <= hr <= .13 and .25 <= xr <= .75 and .48 <= yr <= .82:
                score=a
                if best is None or score>best[0]:
                    best=(score,int(cx),int(cy))
        return None if best is None else (best[1],best[2])

    def hazards(self, frame):
        """Find the spiky purple puffer/zombie hazards, while rejecting purple pipes/coral.

        The supplied recordings show the hazard as a ~square purple component with
        low contour circularity/solidity because of its spikes. Static purple pipes
        are noticeably smoother/more solid.
        """
        hsv=self._hsv(frame)
        H,S,V=cv2.split(hsv)
        mask=((H>=125)&(H<=179)&(S>=70)&(V>=50)).astype(np.uint8)*255
        h,w=mask.shape
        mask[:int(.07*h)]=0
        mask[int(.93*h):]=0
        cnts,_=cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
        found=[]
        for cnt in cnts:
            area=float(cv2.contourArea(cnt))
            if area <= 0:
                continue
            x,y,bw,bh=cv2.boundingRect(cnt)
            ratio=area/(w*h)
            aspect=bw/max(1.0,bh)
            if not (0.006 <= ratio <= 0.030 and 0.70 <= aspect <= 1.40):
                continue
            peri=cv2.arcLength(cnt,True)
            if peri <= 0:
                continue
            circ=4*math.pi*area/(peri*peri)
            hull=cv2.convexHull(cnt)
            hull_area=cv2.contourArea(hull)
            solidity=area/hull_area if hull_area>0 else 1.0
            # Tuned on the two supplied recordings:
            # hazard ~ circularity .27-.33 / solidity .77-.82;
            # purple pipe cluster ~ .49 / .91.
            if circ <= .42 and solidity <= .86:
                cx=int(x+bw/2); cy=int(y+bh/2)
                radius=int(max(bw,bh)*.70)
                found.append((cx,cy,radius))
        return tuple(found)

    def detect(self, frame):
        hsv=self._hsv(frame)
        h,w=frame.shape[:2]
        H,S,V=cv2.split(hsv)
        dark=float(np.mean(V<70))

        blue_popup, popup_close=self._large_blue_popup(hsv)
        if blue_popup:
            return Detection("NOT_ENOUGH", .98, popup_close=popup_close)

        if self._win_machine(hsv):
            # The close X is fixed at upper-right in supplied recordings.
            return Detection("WIN_MACHINE", .97, popup_close=(int(w*.965),int(h*.085)))
