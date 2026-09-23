from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parent
BUILD = "adb-direct-smart-economy-3"
CONFIG_PATH = ROOT / "config.json"
STATE_PATH = ROOT / "progress_state.json"
LOG_PATH = ROOT / "sea_explorer.log"


class BotError(RuntimeError):
    pass


class StopRequested(BotError):
    """The dashboard requested a clean stop."""


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


def _hidden_subprocess_kwargs() -> dict:
    """Keep ADB helper processes invisible when the dashboard uses pythonw."""
    if os.name != "nt":
        return {}
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    return {
        "startupinfo": startup,
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
    }


class ADB:
    def __init__(self, configured_path: str = ""):
        self.adb = self._find_adb(configured_path)
        self.serial = None

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
                r = subprocess.run(
                    [str(p), "version"], capture_output=True, text=True, timeout=5,
                    **_hidden_subprocess_kwargs(),
                )
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
            r = subprocess.run(
                self._cmd(*args), capture_output=True, text=text, timeout=timeout,
                **_hidden_subprocess_kwargs(),
            )
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

    def ensure_ready(self, requested_serial=None):
        ready = [s for s, st in self.devices() if st == "device"]
        if requested_serial:
            if requested_serial not in ready:
                raise BotError(f"Selected device {requested_serial!r} is not connected and authorized.")
            self.serial = requested_serial
            return
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

    def swipe(self, x1, y1, x2, y2, duration_ms=180):
        """Send one real Android touchscreen swipe directly through ADB."""
        self.run(
            "shell", "input", "touchscreen", "swipe",
            int(x1), int(y1), int(x2), int(y2), int(duration_ms),
            timeout=5,
        )

    def back(self):
        self.run("shell", "input", "keyevent", "BACK")

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


class ADBTouch:
    """ScrcpyTouch-compatible adapter backed only by Android ADB input."""

    def __init__(self, adb: ADB):
        self.adb=adb
        self.position: tuple[int,int] | None=None
        self.active=False

    def begin(self, x, y, w, h):
        self.position=(int(x),int(y))
        self.active=True

    def move_to(self, x, y, duration_ms, w, h):
        target=(int(x),int(y))
        if self.position is None:
            self.position=target
            self.active=True
            return
        start=self.position
        self.adb.swipe(start[0],start[1],target[0],target[1],duration_ms)
        self.position=target
        self.active=True

    def release(self):
        self.position=None
        self.active=False


@dataclass
class Progress:
    schema_version: int = 2
    package: str = ""
    oxygen_level_estimate: int = 210
    oxygen_purchases: int = 0
    bag_purchases: int = 0
    runs_completed: int = 0
    last_bag_attempt_run: int = -999

    @classmethod
    def load(cls, starting_oxygen: int):
        if STATE_PATH.exists():
            try:
                data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
                if int(data.get("schema_version", 0)) == 2:
                    return cls(**{k: data[k] for k in cls.__annotations__ if k in data})
                log(
                    "Ignoring progress_state.json from the older buggy build; "
                    f"resetting oxygen estimate to verified live level {starting_oxygen}."
                )
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
        self._digit_templates = None
        template_path = ROOT / "assets" / "coin_digits.npz"
        try:
            with np.load(template_path) as data:
                templates = np.asarray(data["templates"], dtype=np.float32)
            if templates.shape == (10, 44, 32):
                self._digit_templates = templates
            else:
                log(f"Ignoring invalid economy OCR templates: {templates.shape}")
        except Exception as exc:
            log(f"Economy OCR templates unavailable; upgrades will be skipped safely: {exc}")

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
            if .48 <= wr <= .99 and .10 <= hr <= .42 and .30 <= cx/w <= .70 and .30 <= cy/h <= .72:
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

    def _win_machine_close(self, hsv):
        """Detect the green X at the top-right of the Win Machine overlay."""
        h,w=hsv.shape[:2]
        mask=self._color_mask(hsv,35,95,90,75)
        keep=np.zeros_like(mask)
        keep[int(.09*h):int(.24*h),int(.82*w):int(.995*w)]=1
        mask=(mask & keep).astype(np.uint8)
        comps=self._components(mask,max(30,int(h*w*.00004)))
        best=None
        for x,y,bw,bh,a,cx,cy in comps:
            wr,hr=bw/w,bh/h
            xr,yr=cx/w,cy/h
            fill=a/max(1,bw*bh)
            if not (.055<=wr<=.17 and .025<=hr<=.11 and .86<=xr<=.98 and .11<=yr<=.22 and fill>=.28):
                continue
            score=fill-abs(xr-.93)-abs(yr-.17)
            if best is None or score>best[0]:
                best=(score,int(cx),int(cy))
        if best is not None:
            return (best[1],best[2])
        return None

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

    def upgrade_signature(self, frame, kind: str) -> np.ndarray:
        """Binary fingerprint of the displayed BAG/OXYGEN level digits.

        Upgrade success is confirmed only when this small digit region changes.
        This prevents a persistent HOME screen or missed NOT-ENOUGH popup from
        being mistaken for a successful purchase.
        """
        h,w=frame.shape[:2]
        if kind == "oxygen":
            x1,x2,y1,y2 = 0.37,0.62,0.665,0.710
        elif kind == "bag":
            x1,x2,y1,y2 = 0.14,0.34,0.665,0.710
        else:
            raise ValueError(f"unknown upgrade kind: {kind}")

        crop=frame[int(y1*h):int(y2*h), int(x1*w):int(x2*w)]
        if crop.size == 0:
            return np.zeros((48,160), np.uint8)
        gray=cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY)
        gray=cv2.resize(gray,(160,48),interpolation=cv2.INTER_AREA)
        _,mask=cv2.threshold(gray,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)
        return mask

    @staticmethod
    def signature_delta(before: np.ndarray, after: np.ndarray) -> float:
        if before.shape != after.shape or before.size == 0:
            return 0.0
        return float(np.mean(before != after))

    @staticmethod
    def _economy_digit_mask(crop: np.ndarray) -> np.ndarray:
        """White/red digit fill used by the HOME coin and upgrade-cost labels."""
        hsv=cv2.cvtColor(crop,cv2.COLOR_BGR2HSV)
        H,S,V=cv2.split(hsv)
        white=(S<115)&(V>145)
        # When the balance is below a price the game renders it red.
        # Keep this deliberately tight so the dark orange panel is rejected.
        red=(V>180)&(S>120)&((H<=3)|(H>=170))
        return ((white|red).astype(np.uint8)*255)

    @staticmethod
    def _normalize_digit(mask: np.ndarray, out_w: int = 32, out_h: int = 44) -> np.ndarray | None:
        ys,xs=np.where(mask>0)
        if len(xs)==0:
            return None
        glyph=mask[ys.min():ys.max()+1,xs.min():xs.max()+1]
        scale=min((out_w-4)/max(1,glyph.shape[1]),(out_h-4)/max(1,glyph.shape[0]))
        new_w=max(1,int(round(glyph.shape[1]*scale)))
        new_h=max(1,int(round(glyph.shape[0]*scale)))
        resized=cv2.resize(glyph,(new_w,new_h),interpolation=cv2.INTER_NEAREST)
        canvas=np.zeros((out_h,out_w),np.float32)
        x=(out_w-new_w)//2
        y=(out_h-new_h)//2
        canvas[y:y+new_h,x:x+new_w]=(resized>0).astype(np.float32)
        return canvas

    def _read_number_roi(self, frame: np.ndarray, roi) -> tuple[int | None, float]:
        if self._digit_templates is None:
            return None,0.0
        h,w=frame.shape[:2]
        x1,y1,x2,y2=map(float,roi)
        crop=frame[
            int(clamp(y1,0,1)*h):int(clamp(y2,0,1)*h),
            int(clamp(x1,0,1)*w):int(clamp(x2,0,1)*w),
        ]
        if crop.size==0:
            return None,0.0
        mask=self._economy_digit_mask(crop)
        n,_,stats,_=cv2.connectedComponentsWithStats(mask,8)
        area_total=max(1,crop.shape[0]*crop.shape[1])
        glyphs=[]
        for i in range(1,n):
            x,y,bw,bh,area=map(int,stats[i])
            if area<max(8,int(area_total*.001)):
                continue
            if bh<crop.shape[0]*.25 or bh>crop.shape[0]*.95 or bw<2 or bw>crop.shape[1]*.60:
                continue
            glyphs.append((x,mask[y:y+bh,x:x+bw]))
        glyphs.sort(key=lambda item:item[0])
        if not glyphs or len(glyphs)>6:
            return None,0.0

        digits=[]
        confidences=[]
        for _,raw in glyphs:
            glyph=self._normalize_digit(raw)
            if glyph is None:
                return None,0.0
            g=glyph.reshape(-1)
            gnorm=float(np.linalg.norm(g))
            best_digit=None
            best_score=-1.0
            for digit,template in enumerate(self._digit_templates):
                t=template.reshape(-1)
                denom=gnorm*float(np.linalg.norm(t))
                score=float(np.dot(g,t)/denom) if denom>0 else 0.0
                if score>best_score:
                    best_score=score
                    best_digit=digit
            digits.append(str(best_digit))
            confidences.append(best_score)

        confidence=min(confidences) if confidences else 0.0
        minimum=float(self.cfg.get("economy_ocr",{}).get("digit_confidence_min",.82))
        if confidence<minimum:
            return None,confidence
        try:
            return int("".join(digits)),confidence
        except ValueError:
            return None,confidence

    def read_home_economy(self, frame: np.ndarray) -> dict:
        """Read current coins and visible upgrade prices without tapping anything."""
        ocr=self.cfg.get("economy_ocr",{})
        fields={
            "coins": ocr.get("coins_roi",[.40,.195,.74,.25]),
            "bag_cost": ocr.get("bag_cost_roi",[.19,.79,.36,.84]),
            "oxygen_cost": ocr.get("oxygen_cost_roi",[.43,.79,.65,.84]),
        }
        result={}
        confidence={}
        for name,roi in fields.items():
            value,score=self._read_number_roi(frame,roi)
            result[name]=value
            confidence[name]=score
        result["confidence"]=confidence
        return result

    def hazards(self, frame, hsv=None):
        """Find purple spiky bombs early without touching the swipe cadence.

        The old detector ignored the bomb until it occupied 0.6% of the whole
        screen, which was unnecessarily late. Keep the same shape rejection for
        pipes/coral but allow smaller, farther-away bombs to trigger avoidance.
        """
        if hsv is None:
            hsv=self._hsv(frame)
        H=hsv[:,:,0]; S=hsv[:,:,1]; V=hsv[:,:,2]
        mask=((H>=125)&(H<=155)&(S>=70)&(V>=50)).astype(np.uint8)*255
        h,w=mask.shape
        mask[:int(.07*h)]=0
        mask[int(.93*h):]=0
        cnts,_=cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
        found=[]
        motion=self.cfg.get("motion",{})
        min_ratio=float(motion.get("hazard_min_area_ratio",.0018))
        max_ratio=float(motion.get("hazard_max_area_ratio",.030))
        for cnt in cnts:
            area=float(cv2.contourArea(cnt))
            if area<=0:
                continue
            x,y,bw,bh=cv2.boundingRect(cnt)
            ratio=area/(w*h)
            aspect=bw/max(1.0,bh)
            if not (min_ratio<=ratio<=max_ratio and .68<=aspect<=1.45):
                continue
            peri=cv2.arcLength(cnt,True)
            if peri<=0:
                continue
            circ=4*math.pi*area/(peri*peri)
            hull=cv2.convexHull(cnt)
            hull_area=cv2.contourArea(hull)
            solidity=area/hull_area if hull_area>0 else 1.0
            # Spiky bomb: irregular outline. Purple pipes are much smoother/solid.
            # Slightly relax only the tiny/far case so it is noticed earlier.
            tiny=ratio<.006
            circ_limit=.46 if tiny else .42
            solidity_limit=.88 if tiny else .86
            if circ<=circ_limit and solidity<=solidity_limit:
                cx=int(x+bw/2); cy=int(y+bh/2)
                radius=int(max(bw,bh)*(.78 if tiny else .70))
                found.append((cx,cy,radius))
        return tuple(found)

    def detect(self, frame):
        hsv=self._hsv(frame)
        h,w=frame.shape[:2]
        V=hsv[:,:,2]
        dark=float(np.mean(V<70))

        # During a dive, skip the expensive large-popup connected-component scan.
        # Reuse this same HSV frame for bomb detection instead of converting twice.
        if self._win_machine(hsv):
            close=self._win_machine_close(hsv)
            if close is None:
                close=(int(w*.93),int(h*.17))
            return Detection("WIN_MACHINE", .98, popup_close=close)

        if self._gameplay_hud(hsv):
            return Detection("PLAYING", .99, hazards=self.hazards(frame,hsv=hsv))

        blue_popup,popup_close=self._large_blue_popup(hsv)
        if blue_popup:
            return Detection("NOT_ENOUGH", .98, popup_close=popup_close)

        if self._home(hsv):
            return Detection("HOME", .97)

        orange=self._lower_orange_button(hsv)
        if dark>=.22 and orange is not None:
            return Detection("RESULT", .90, orange_button=orange)

        if dark>=.30:
            return Detection("TRANSITION", .65)

        return Detection("UNKNOWN", .30)


class Sweeper:
    """Continuous ADB sweep worker with asynchronous hazard supervision.

    Vision and motion run independently: ADB screenshots can take a noticeable
    fraction of a second, but the movement worker keeps alternating left/right
    during that time. Hazard observations can park or escape the worker at the
    boundary of the current short swipe.
    """

    def __init__(self, touch: ADBTouch, cfg: dict):
        self.touch=touch
        self.cfg=cfg
        self.last_target=None
        self.active=False
        self.release_failed=False
        self.control_y=float(cfg["motion"].get("control_y",0.67))
        self.swipes_sent=0
        self.blocked=False
        self.clear_frames=0
        self.escape_y=None

        self._lock=threading.Lock()
        self._stop_worker=threading.Event()
        self._wake_worker=threading.Event()
        self._worker=None
        self._frame_size=None
        self._threat_cache=()
        self._status="IDLE"
        self._worker_error=None

    def _start_worker_if_needed(self, w, h):
        if self._worker is not None and self._worker.is_alive():
            return
        self._stop_worker.clear()
        self._wake_worker.clear()
        self._frame_size=(w,h)
        start=self.last_target or self._px((.5,self.control_y),w,h)
        if not self.touch.active:
            self.touch.begin(*start,w,h)
        self.last_target=start
        self.active=True
        self._worker=threading.Thread(
            target=self._motion_loop,
            name="SeaExplorer-ADB-Sweep",
            daemon=True,
        )
        self._worker.start()

    def stop(self):
        self._stop_worker.set()
        self._wake_worker.set()
        worker=self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=1.5)
        release_error=None
        try:
            if self.touch is not None:
                self.touch.release()
        except Exception as exc:
            release_error=exc
            self.release_failed=True
            log(f"Could not stop ADB touch adapter: {exc}")

        with self._lock:
            self.active=False
            self.last_target=None
            self.blocked=False
            self.clear_frames=0
            self.escape_y=None
            self._threat_cache=()
            self._frame_size=None
            self._status="IDLE"
            self._worker=None

        if worker is not None and worker.is_alive():
            self.release_failed=True
            raise BotError("ADB movement worker did not stop within 1.5 seconds")
        if release_error is not None:
            raise BotError("Could not stop ADB movement controller") from release_error

    def _px(self, norm, w, h):
        return (
            int(clamp(float(norm[0]),.02,.98)*w),
            int(clamp(float(norm[1]),.08,.92)*h),
        )

    @staticmethod
    def _clearance(start, end, hazards, margin):
        ax,ay=start; bx,by=end
        dx=bx-ax; dy=by-ay
        length2=dx*dx+dy*dy
        best=float("inf")
        for hx,hy,radius in hazards:
            t=clamp(((hx-ax)*dx+(hy-ay)*dy)/length2,0.0,1.0) if length2 else 0.0
            distance=math.hypot(hx-(ax+t*dx),hy-(ay+t*dy))
            best=min(best,distance-radius-margin)
        return best

    def _threats(self, hazards, h):
        m=self.cfg["motion"]
        row=self.control_y*h
        ahead=float(m.get("danger_ahead_ratio",.40))*h
        behind=float(m.get("danger_behind_ratio",.12))*h
        return tuple(v for v in hazards if row-ahead <= v[1] <= row+behind)

    @staticmethod
    def _horizontal_clearance(x, hazards, margin):
        if not hazards:
            return float("inf")
        return min(abs(x-hx)-radius-margin for hx,_,radius in hazards)

    def _motion_loop(self):
        try:
            while not self._stop_worker.is_set():
                with self._lock:
                    frame_size=self._frame_size
                    blocked=self.blocked
                    threats=tuple(self._threat_cache)
                    start=self.last_target
                    escape_y=self.escape_y

                if frame_size is None:
                    self._wake_worker.wait(.01)
                    self._wake_worker.clear()
                    continue

                w,h=frame_size
                m=self.cfg["motion"]
                row_y=int(self.control_y*h)
                left=self._px((m["x_left"],self.control_y),w,h)[0]
                right=self._px((m["x_right"],self.control_y),w,h)[0]
                margin=float(m.get("danger_radius_ratio",.12))*w
                sweep_ms=int(m.get("sweep_ms",110))
                escape_ms=int(m.get("escape_ms",120))
                start=start or (w//2,row_y)

                if blocked:
                    target=start
                    reason="PARK-CONFIRM"
                    new_escape=escape_y

                    if threats:
                        if escape_y is not None:
                            reason="PARK-ESCAPED"
                        elif self._horizontal_clearance(start[0],threats,margin) > 0:
                            reason="PARK-SAFE"
                        else:
                            safe_edge=max(
                                (left,right),
                                key=lambda x:self._horizontal_clearance(x,threats,margin),
                            )
                            edge=(safe_edge,start[1])
                            lookahead=float(m.get("gesture_lookahead_ratio",.06))*h
                            if (
                                self._horizontal_clearance(safe_edge,threats,margin)>0
                                and self._clearance(start,edge,threats,margin+lookahead)>0
                            ):
                                target=edge
                                reason="PARK-MOVE"
                            else:
                                nearest=min(threats,key=lambda v:math.dist(start,(v[0],v[1])))
                                candidate_y=.52 if nearest[1]>start[1] else .82
                                escape=(start[0],int(candidate_y*h))
                                start_clear=self._clearance(start,start,threats,margin)
                                path_clear=self._clearance(start,escape,threats,margin)
                                end_clear=self._clearance(escape,escape,threats,margin)
                                if end_clear>start_clear and path_clear>=start_clear-2:
                                    target=escape
                                    new_escape=candidate_y
                                    reason="ESCAPE"
                                else:
                                    reason="HOLD-TRAPPED"

                    if target != start:
                        self.touch.move_to(
                            *target,
                            escape_ms if reason=="ESCAPE" else sweep_ms,
                            w,h,
                        )
                        with self._lock:
                            self.last_target=target
                            self.escape_y=new_escape

                    with self._lock:
                        self._status=f"{reason} hazards={len(threats)}"
                    self._wake_worker.wait(float(m.get("blocked_poll_s",.015)))
                    self._wake_worker.clear()
                    continue

                # Once danger clears, return from a temporary vertical escape,
                # then resume uninterrupted alternating horizontal swipes.
                if escape_y is not None and start[1] != row_y:
                    target=(start[0],row_y)
                    self.touch.move_to(*target,escape_ms,w,h)
                    with self._lock:
                        self.last_target=target
                        self.escape_y=None
                        self._status="RETURN-LANE"
                    continue

                target=(right,row_y) if start[0] <= (left+right)/2 else (left,row_y)
                self.touch.move_to(*target,sweep_ms,w,h)
                with self._lock:
                    self.last_target=target
                    self.swipes_sent+=1
                    direction="RIGHT" if target[0] == right else "LEFT"
                    self._status=f"CONTINUOUS-{direction} #{self.swipes_sent}"
        except Exception as exc:
            with self._lock:
                self._worker_error=exc
                self._status=f"MOTION-ERROR {type(exc).__name__}"
            self._stop_worker.set()

    def step(self, frame, hazards, dry_run=False):
        h,w=frame.shape[:2]
        threats=self._threats(hazards,h)

        with self._lock:
            self._frame_size=(w,h)
            self._threat_cache=threats
            if threats:
                self.blocked=True
                self.clear_frames=0
            elif self.blocked:
                self.clear_frames+=1
                if self.clear_frames>=int(self.cfg["motion"].get("clear_frames",2)):
                    self.blocked=False
                    self.clear_frames=0
            status=self._status
            target=self.last_target
            worker_error=self._worker_error

        if worker_error is not None:
            raise BotError(f"Continuous ADB movement failed: {worker_error}") from worker_error

        if dry_run:
            if threats:
                return f"DRY-PARK hazards={len(threats)}", target or (w//2,int(self.control_y*h))
            return "DRY-CONTINUOUS-SWEEP", target or (w//2,int(self.control_y*h))

        self._start_worker_if_needed(w,h)
        self._wake_worker.set()
        with self._lock:
            return self._status, self.last_target or (w//2,int(self.control_y*h))


class SeaExplorerBot:
    def __init__(self, cfg, dry_run=False, *, serial=None, window_title=None,
                 stop_event=None, transport=None):
        self.cfg=cfg
        self.dry_run=dry_run
        self.stop_event=stop_event
        self.adb=ADB(cfg.get("adb_path",""))
        self.adb.ensure_ready(serial)
        if transport != "wireless" and ":" not in self.adb.serial:
            self.adb.keep_awake_usb()
        self.vision=Vision(cfg)
        self.progress=Progress.load(int(cfg["starting_oxygen_level"]))
        # window_title is accepted only for backward CLI compatibility.
        self.sweeper=Sweeper(None if dry_run else ADBTouch(self.adb),cfg)
        self.prev_state=None
        self.playing_unknown_since=None
        self.home_handled=False
        self.last_start_attempt=0.0
        self.unknown_since=None
        self.last_recovery=0.0

    def _check_stop(self):
        if self.stop_event is not None and self.stop_event.is_set():
            raise StopRequested("Stop requested from dashboard")

    def _sleep(self, seconds):
        self._check_stop()
        if self.stop_event is None:
            time.sleep(seconds)
        elif self.stop_event.wait(max(0.0,float(seconds))):
            raise StopRequested("Stop requested from dashboard")

    def _frame(self):
        self._check_stop()
        return self.adb.screenshot()

    def _tap_norm(self, frame, pair):
        self._check_stop()
        h,w=frame.shape[:2]
        x=int(float(pair[0])*w); y=int(float(pair[1])*h)
        if self.dry_run:
            log(f"DRY TAP ({x},{y})")
        else:
            self.adb.tap(x,y)

    def _resolve_package(self):
        if self.progress.package:
            return self.progress.package
        configured=self.cfg.get("package","").strip()
        if configured:
            self.progress.package=configured
            self.progress.save()
            return configured
        package=self.adb.foreground_package()
        if not package or package.startswith(("com.android.","com.google.android.","com.brave.")):
            raise BotError("Open Sea Explorer on the phone first, then run the bot once. It will remember the package automatically.")
        self.progress.package=package
        self.progress.save()
        log(f"Detected game package: {package}")
        return package

    def _bag_cadence(self):
        level=self.progress.oxygen_level_estimate
        tiers=self.cfg["economy"]["bag_every_runs"]
        for row in tiers:
            if level < int(row["until_oxygen"]):
                return int(row["every_runs"])
        return int(tiers[-1]["every_runs"])

    def _popup_close(self, frame, det):
        self._check_stop()
        if det.popup_close:
            x,y=det.popup_close
            if self.dry_run: log(f"DRY close popup ({x},{y})")
            else: self.adb.tap(x,y)
        else:
            self._tap_norm(frame,self.cfg["taps"]["popup_close_fallback"])

    def _attempt_upgrade(self, frame, kind):
        self._check_stop()
        tap=self.cfg["taps"][f"{kind}_upgrade"]
        before_sig=self.vision.upgrade_signature(frame,kind)
        log(f"Attempting {kind} upgrade...")
        self._tap_norm(frame,tap)
        if self.dry_run:
            return False

        deadline=time.monotonic()+float(self.cfg["economy"]["upgrade_result_timeout_s"])
        best_delta=0.0
        change_threshold=float(self.cfg["economy"].get("upgrade_signature_change_min",0.0035))

        while time.monotonic()<deadline:
            self._sleep(.25)
            after=self.adb.screenshot()
            d=self.vision.detect(after)

            if d.state=="NOT_ENOUGH":
                log(f"{kind}: not enough coins.")
                self._popup_close(after,d)
                self._sleep(.35)
                return False

            if d.state=="HOME":
                after_sig=self.vision.upgrade_signature(after,kind)
                delta=self.vision.signature_delta(before_sig,after_sig)
                best_delta=max(best_delta,delta)
                if delta >= change_threshold:
                    if kind=="oxygen":
                        self.progress.oxygen_purchases += 1
                        self.progress.oxygen_level_estimate += int(self.cfg["oxygen_step"])
                        log(
                            f"oxygen upgrade visually confirmed "
                            f"(digit_delta={delta:.4f}) -> level "
                            f"{self.progress.oxygen_level_estimate}"
                        )
                    else:
                        self.progress.bag_purchases += 1
                        log(
                            f"bag upgrade visually confirmed "
                            f"(digit_delta={delta:.4f}) -> "
                            f"+{self.progress.bag_purchases} automated bag upgrades"
                        )
                    self.progress.save()
                    return True

        log(
            f"{kind}: purchase NOT confirmed "
            f"(level digits unchanged; best_delta={best_delta:.4f}). "
            "Not counting it."
        )
        return False

    def _start_next_dive(self, frame) -> bool:
        """Tap TAP TO DROP and verify that HOME actually disappears."""
        self._check_stop()
        now=time.monotonic()
        if now-self.last_start_attempt < float(self.cfg["timing"].get("start_retry_cooldown_s",2.0)):
            return False
        self.last_start_attempt=now

        candidates=[self.cfg["taps"]["start_run"]]
        candidates.extend(self.cfg["taps"].get("start_run_fallbacks",[]))

        for idx,pair in enumerate(candidates,1):
            self._check_stop()
            log(f"Starting next dive: TAP TO DROP attempt {idx}/{len(candidates)}.")
            self._tap_norm(frame,pair)
            if self.dry_run:
                return False

            deadline=time.monotonic()+float(self.cfg["timing"].get("start_verify_timeout_s",1.8))
            while time.monotonic()<deadline:
                self._sleep(.25)
                after=self.adb.screenshot()
                d=self.vision.detect(after)
                if d.state=="PLAYING":
                    # The HUD appears before the fading HOME animation is
                    # finished. A stroke during that transition can be ignored,
                    # leaving the next touch-down at the wrong edge.
                    self._sleep(float(self.cfg["timing"].get("after_start_tap_s",.8)))
                    log("Dive start visually confirmed: PLAYING; animation settled.")
                    return True
                if d.state not in {"HOME","UNKNOWN","TRANSITION"}:
                    log(f"Dive start left HOME -> {d.state}; handing back to state loop.")
                    return True

        log("TAP TO DROP did not start the dive; will retry without rebuying upgrades.")
        return False

    def _handle_home(self, frame):
        self._check_stop()
        self.sweeper.stop()

        goal=int(self.cfg["goal_oxygen_level"])

        if self.home_handled:
            self._start_next_dive(frame)
            return
        self.home_handled=True
        if self.progress.oxygen_level_estimate>=goal:
            log(f"GOAL REACHED: estimated oxygen level {self.progress.oxygen_level_estimate} >= {goal}")
            raise SystemExit(0)

        economy=self.cfg.get("economy",{})
        if not bool(economy.get("buy_upgrades",False)):
            log("Upgrade buying is OFF -> starting next dive immediately.")
            self._start_next_dive(frame)
            return

        mode=str(economy.get("upgrade_mode","smart")).lower()
        if mode not in {"smart","bag","oxygen"}:
            mode="smart"

        def snapshot(current_frame):
            values=self.vision.read_home_economy(current_frame)
            log(
                "HOME economy: "
                f"coins={values.get('coins')} "
                f"bag={values.get('bag_cost')} "
                f"oxygen={values.get('oxygen_cost')} "
                f"mode={mode}"
            )
            return values

        values=snapshot(frame)
        if values.get("coins") is None:
            log("Could not read coin balance confidently -> skipping upgrades, no blind taps.")
            self._start_next_dive(frame)
            return

        cadence=self._bag_cadence()
        bag_due=(self.progress.runs_completed-self.progress.last_bag_attempt_run)>=cadence
        if mode in {"smart","bag"} and bag_due and self.progress.runs_completed>0:
            coins=values.get("coins")
            cost=values.get("bag_cost")
            if cost is None:
                log("Bag price unreadable -> skipping bag purchase safely.")
            elif coins<cost:
                log(f"Bag upgrade skipped: {coins} coins < {cost} required.")
            else:
                self.progress.last_bag_attempt_run=self.progress.runs_completed
                self.progress.save()
                if self._attempt_upgrade(frame,"bag"):
                    self._sleep(.15)
                    frame=self.adb.screenshot()
                    values=snapshot(frame)

        if mode in {"smart","oxygen"}:
            for _ in range(int(economy.get("max_oxygen_purchases_per_home",8))):
                self._check_stop()
                if self.progress.oxygen_level_estimate>=goal:
                    log(f"GOAL REACHED: estimated oxygen level {self.progress.oxygen_level_estimate} >= {goal}")
                    raise SystemExit(0)

                coins=values.get("coins")
                cost=values.get("oxygen_cost")
                if coins is None or cost is None:
                    log("Coin/oxygen price unreadable -> stopping purchase loop with no blind tap.")
                    break
                if coins<cost:
                    log(f"Oxygen upgrade skipped: {coins} coins < {cost} required.")
                    break

                ok=self._attempt_upgrade(frame,"oxygen")
                if not ok:
                    break
                self._sleep(.15)
                frame=self.adb.screenshot()
                values=snapshot(frame)

        self._start_next_dive(frame)

    def _recover_unknown(self, frame):
        self._check_stop()
        now=time.monotonic()
        if self.unknown_since is None:
            self.unknown_since=now
            return
        age=now-self.unknown_since
        if age >= float(self.cfg["timing"]["unknown_back_after_s"]) and now-self.last_recovery>8:
            self.last_recovery=now
            self.sweeper.stop()
            log(f"UNKNOWN persisted {age:.1f}s -> Android Back")
            if not self.dry_run:
                self.adb.back()
        if age >= float(self.cfg["timing"]["unknown_relaunch_after_s"]) and now-self.last_recovery>8:
            self.last_recovery=now
            self.sweeper.stop()
            package=self._resolve_package()
            log(f"UNKNOWN persisted {age:.1f}s -> relaunch {package}")
            if not self.dry_run:
                self.adb.start_app(package)
            self.unknown_since=now

    def run(self):
        package=self._resolve_package()
        log(
            f"START package={package} dry_run={self.dry_run} "
            f"oxygen_est={self.progress.oxygen_level_estimate}/{self.cfg['goal_oxygen_level']} "
            f"runs={self.progress.runs_completed}"
        )
        last_status=0.0
        try:
            while True:
                self._check_stop()
                frame=self._frame()
                det=self.vision.detect(frame)
                now=time.monotonic()

                # One unreadable gameplay frame must not lift and restart the
                # finger. The next local mirror frame arrives quickly.
                if det.state=="UNKNOWN" and self.prev_state=="PLAYING":
                    if self.playing_unknown_since is None:
                        self.playing_unknown_since=now
                    grace=float(self.cfg["timing"].get("playing_unknown_grace_s",.6))
                    if now-self.playing_unknown_since<grace:
                        self._sleep(.03)
                        continue
                else:
                    self.playing_unknown_since=None

                if det.state!="UNKNOWN":
                    self.unknown_since=None

                if det.state != self.prev_state:
                    log(f"STATE {self.prev_state or '-'} -> {det.state} conf={det.confidence:.2f}")
                    if self.prev_state=="PLAYING" and det.state in {"RESULT","TRANSITION","WIN_MACHINE","HOME"}:
                        self.progress.runs_completed += 1
                        self.progress.save()
                        log(f"Completed run #{self.progress.runs_completed}")
                    if det.state!="HOME":
                        self.home_handled=False
                    self.prev_state=det.state

                if det.state=="PLAYING":
                    reason,target=self.sweeper.step(frame,det.hazards,self.dry_run)
                    if now-last_status>=1.0:
                        log(f"{reason} target={target} hazards={len(det.hazards)}")
                        last_status=now
                    self._sleep(float(self.cfg["timing"]["gameplay_loop_s"]))
                    continue

                self.sweeper.stop()

                if det.state=="HOME":
                    self._handle_home(frame)
                elif det.state=="RESULT":
                    if det.orange_button:
                        log(f"Claiming run reward at {det.orange_button}")
                        if not self.dry_run:
                            self.adb.tap(*det.orange_button)
                        self._sleep(float(self.cfg["timing"]["after_result_tap_s"]))
                elif det.state=="WIN_MACHINE":
                    log("Closing optional Win Machine; never pressing SPIN/ad.")
                    self._popup_close(frame,det)
                    self._sleep(.6)
                elif det.state=="NOT_ENOUGH":
                    self._popup_close(frame,det)
                    self._sleep(.4)
                elif det.state=="UNKNOWN":
                    self._recover_unknown(frame)
                else:
                    # RESULT animations/loading should be allowed to settle.
                    self._sleep(.35)

                self._sleep(float(self.cfg["timing"]["non_gameplay_loop_s"]))
        finally:
            try:
                self.sweeper.stop()
            finally:
                self.progress.save()
                if self.sweeper.release_failed:
                    log("STOP; ADB controller cleanup failed; progress saved.")
                else:
                    log("STOP; ADB direct controller stopped; progress saved.")


def analyze_video(path, cfg, every_s=1.0):
    cap=cv2.VideoCapture(path)
    if not cap.isOpened():
        raise BotError(f"Cannot open video: {path}")
    fps=cap.get(cv2.CAP_PROP_FPS) or 30.0
    frames=int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration=frames/fps
    vision=Vision(cfg)
    counts={}
    hazard_frames=0
    max_hazards=0
    t=0.0
    while t<=duration:
        cap.set(cv2.CAP_PROP_POS_MSEC,t*1000)
        ok,fr=cap.read()
        if not ok:
            break
        d=vision.detect(fr)
        counts[d.state]=counts.get(d.state,0)+1
        if d.hazards:
            hazard_frames+=1
            max_hazards=max(max_hazards,len(d.hazards))
        t+=every_s
    cap.release()
    print(json.dumps({
        "video": str(path),
        "duration_s": round(duration,2),
        "sample_interval_s": every_s,
        "states": counts,
        "hazard_samples": hazard_frames,
        "max_hazards_in_sample": max_hazards,
    }, indent=2))


def main():
    p=argparse.ArgumentParser(description="Sea Explorer autonomous sweeper + purple-hazard avoidance + oxygen upgrade bot")
    p.add_argument("--dry-run",action="store_true",help="detect/plan but do not tap or move")
    p.add_argument("--analyze-video",metavar="MP4",help="offline diagnostic against a recording; no phone required")
    p.add_argument("--sample-every",type=float,default=1.0,help="seconds between offline video samples")
    p.add_argument("--serial",help="exact authorized ADB serial to control")
    p.add_argument("--transport",choices=("usb","wireless"),help="selected connection transport")
    p.add_argument("--window-title",help="legacy option; ignored in ADB direct mode")
    args=p.parse_args()

    cfg=load_config()
    if args.analyze_video:
        analyze_video(args.analyze_video,cfg,max(.2,args.sample_every))
        return 0

    try:
        SeaExplorerBot(cfg,dry_run=args.dry_run,serial=args.serial,
                       transport=args.transport,window_title=args.window_title).run()
        return 0
    except StopRequested:
        print("Stopped.")
        return 0
    except SystemExit as e:
        return int(e.code or 0)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 130
    except Exception as e:
        log(f"ERROR {type(e).__name__}: {e}")
        return 1


if __name__=="__main__":
    raise SystemExit(main())
