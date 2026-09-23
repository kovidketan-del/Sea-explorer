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
BUILD = "one-go-livefix-2"
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
    schema_version: int = 2
    package: str = ""
    oxygen_level_estimate: int = 190
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

        if self._gameplay_hud(hsv):
            return Detection("PLAYING", .99, hazards=self.hazards(frame))

        if self._home(hsv):
            return Detection("HOME", .97)

        orange=self._lower_orange_button(hsv)
        if dark >= .22 and orange is not None:
            return Detection("RESULT", .90, orange_button=orange)

        # A darkened transitional/reward frame without its button fully visible yet.
        if dark >= .30:
            return Detection("TRANSITION", .65)

        return Detection("UNKNOWN", .30)


class Sweeper:
    def __init__(self, adb: ADB, cfg: dict):
        self.adb=adb
        self.cfg=cfg
        self.active=False
        self.points=[]
        self.index=0
        self.last_target=None
        self._make_points()

    def _make_points(self):
        m=self.cfg["motion"]
        left=float(m["x_left"]); right=float(m["x_right"])
        rows=[float(x) for x in m["rows"]]
        pts=[]
        for i,y in enumerate(rows):
            if i%2==0:
                pts += [(left,y),(right,y)]
            else:
                pts += [(right,y),(left,y)]
        self.norm_points=pts

    def stop(self):
        if self.active and self.adb.supports_motionevent():
            try:
                if self.last_target:
                    self.adb.motionevent("UP",*self.last_target,check=False)
                else:
                    self.adb.motionevent("UP",1,1,check=False)
            except Exception:
                pass
        self.active=False

    def _px(self, norm, w, h):
        return (int(clamp(norm[0],.02,.98)*w),int(clamp(norm[1],.08,.92)*h))

    def _danger(self, point, hazards, w):
        danger=float(self.cfg["motion"]["danger_radius_ratio"])*w
        return any(math.hypot(point[0]-hx,point[1]-hy) <= danger+hr for hx,hy,hr in hazards)

    def _flee(self, hazards, w, h):
        m=self.cfg["motion"]
        bounds=[
            self._px((m["x_left"], m["rows"][0]),w,h),
            self._px((m["x_right"], m["rows"][0]),w,h),
            self._px((m["x_left"], m["rows"][-1]),w,h),
            self._px((m["x_right"], m["rows"][-1]),w,h),
        ]
        base=self.last_target or (w//2,h//2)
        # Pick the safe corner maximizing distance from all detected hazards and
        # mildly preferring a shorter trip from our last commanded position.
        best=None
        for p in bounds:
            if not hazards:
                score=0
            else:
                score=min(math.hypot(p[0]-hx,p[1]-hy)-hr for hx,hy,hr in hazards)
            score -= .12*math.hypot(p[0]-base[0],p[1]-base[1])
            if best is None or score>best[0]:
                best=(score,p)
        return best[1]

    def step(self, frame, hazards, dry_run=False):
        h,w=frame.shape[:2]
        if hazards:
            target=self._flee(hazards,w,h)
            reason=f"EVADE {len(hazards)} purple hazard(s)"
        else:
            target=None
            # Skip sweep points too close to a detected hazard. Normally the
            # list is empty here, but this keeps the rule explicit.
            for _ in range(len(self.norm_points)):
                p=self._px(self.norm_points[self.index],w,h)
                self.index=(self.index+1)%len(self.norm_points)
                if not self._danger(p,hazards,w):
                    target=p
                    break
            if target is None:
                target=(w//2,int(h*.18))
            reason="SWEEP"

        if dry_run:
            self.last_target=target
            return reason,target

        if self.adb.supports_motionevent():
            if not self.active:
                self.adb.motionevent("DOWN",*target)
                self.active=True
            else:
                self.adb.motionevent("MOVE",*target)
        else:
            start=self.last_target or (w//2,h//2)
            self.adb.swipe(start[0],start[1],target[0],target[1],ms=int(self.cfg["motion"]["fallback_swipe_ms"]))
        self.last_target=target
        return reason,target


class SeaExplorerBot:
    def __init__(self, cfg, dry_run=False):
        self.cfg=cfg
        self.dry_run=dry_run
        self.adb=ADB(cfg.get("adb_path",""))
        self.adb.ensure_ready()
        self.adb.keep_awake_usb()
        self.vision=Vision(cfg)
        self.progress=Progress.load(int(cfg["starting_oxygen_level"]))
        self.sweeper=Sweeper(self.adb,cfg)
        self.prev_state=None
        self.home_handled=False
        self.last_start_attempt=0.0
        self.unknown_since=None
        self.last_recovery=0.0

    def _tap_norm(self, frame, pair):
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
        if det.popup_close:
            x,y=det.popup_close
            if self.dry_run: log(f"DRY close popup ({x},{y})")
            else: self.adb.tap(x,y)
        else:
            self._tap_norm(frame,self.cfg["taps"]["popup_close_fallback"])

    def _attempt_upgrade(self, frame, kind):
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
            time.sleep(.25)
            after=self.adb.screenshot()
            d=self.vision.detect(after)

            if d.state=="NOT_ENOUGH":
                log(f"{kind}: not enough coins.")
                self._popup_close(after,d)
                time.sleep(.35)
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
        now=time.monotonic()
        if now-self.last_start_attempt < float(self.cfg["timing"].get("start_retry_cooldown_s",2.0)):
            return False
        self.last_start_attempt=now

        candidates=[self.cfg["taps"]["start_run"]]
        candidates.extend(self.cfg["taps"].get("start_run_fallbacks",[]))

        for idx,pair in enumerate(candidates,1):
            log(f"Starting next dive: TAP TO DROP attempt {idx}/{len(candidates)}.")
            self._tap_norm(frame,pair)
            if self.dry_run:
                return False

            deadline=time.monotonic()+float(self.cfg["timing"].get("start_verify_timeout_s",1.8))
            while time.monotonic()<deadline:
                time.sleep(.25)
                after=self.adb.screenshot()
                d=self.vision.detect(after)
                if d.state=="PLAYING":
                    log("Dive start visually confirmed: PLAYING.")
                    return True
                if d.state not in {"HOME","UNKNOWN","TRANSITION"}:
                    log(f"Dive start left HOME -> {d.state}; handing back to state loop.")
                    return True

        log("TAP TO DROP did not start the dive; will retry without rebuying upgrades.")
        return False

    def _handle_home(self, frame):
        self.sweeper.stop()

        goal=int(self.cfg["goal_oxygen_level"])

        # Upgrades are handled only once per HOME visit. If TAP TO DROP misses,
        # later HOME loops retry starting the dive without spending again.
        if self.home_handled:
            self._start_next_dive(frame)
            return
        self.home_handled=True
        if self.progress.oxygen_level_estimate >= goal:
            log(f"GOAL REACHED: estimated oxygen level {self.progress.oxygen_level_estimate} >= {goal}")
            raise SystemExit(0)

        cadence=self._bag_cadence()
        due=(self.progress.runs_completed-self.progress.last_bag_attempt_run)>=cadence
        if due and self.progress.runs_completed>0:
            self.progress.last_bag_attempt_run=self.progress.runs_completed
            self.progress.save()
            # Bag is the income engine. In the recordings, 7 slots fill long
            # before 170 oxygen is exhausted, so invest periodically.
            self._attempt_upgrade(frame,"bag")
            time.sleep(.35)
            frame=self.adb.screenshot()

        # Oxygen is the actual objective. Spend as much as currently affordable,
        # but cap the batch so a single HOME visit cannot loop forever.
        for _ in range(int(self.cfg["economy"]["max_oxygen_purchases_per_home"])):
            if self.progress.oxygen_level_estimate >= goal:
                log(f"GOAL REACHED: estimated oxygen level {self.progress.oxygen_level_estimate} >= {goal}")
                raise SystemExit(0)
            ok=self._attempt_upgrade(frame,"oxygen")
            if not ok:
                break
            time.sleep(.25)
            frame=self.adb.screenshot()

        # Drop back in for another earning run, and verify it actually started.
        self._start_next_dive(frame)

    def _recover_unknown(self, frame):
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
                frame=self.adb.screenshot()
                det=self.vision.detect(frame)
                now=time.monotonic()

                if det.state!="UNKNOWN":
                    self.unknown_since=None

                if det.state != self.prev_state:
                    log(f"STATE {self.prev_state or '-'} -> {det.state} conf={det.confidence:.2f}")
                    if self.prev_state=="PLAYING" and det.state in {"RESULT","TRANSITION"}:
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
                    time.sleep(float(self.cfg["timing"]["gameplay_loop_s"]))
                    continue

                self.sweeper.stop()

                if det.state=="HOME":
                    self._handle_home(frame)
                elif det.state=="RESULT":
                    if det.orange_button:
                        log(f"Claiming run reward at {det.orange_button}")
                        if not self.dry_run:
                            self.adb.tap(*det.orange_button)
                        time.sleep(float(self.cfg["timing"]["after_result_tap_s"]))
                elif det.state=="WIN_MACHINE":
                    log("Closing optional Win Machine; never pressing SPIN/ad.")
                    self._popup_close(frame,det)
                    time.sleep(.6)
                elif det.state=="NOT_ENOUGH":
                    self._popup_close(frame,det)
                    time.sleep(.4)
                elif det.state=="UNKNOWN":
                    self._recover_unknown(frame)
                else:
                    # RESULT animations/loading should be allowed to settle.
                    time.sleep(.35)

                time.sleep(float(self.cfg["timing"]["non_gameplay_loop_s"]))
        finally:
            self.sweeper.stop()
            self.progress.save()
            log("STOP; touch released and progress saved.")


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
    args=p.parse_args()

    cfg=load_config()
    if args.analyze_video:
        analyze_video(args.analyze_video,cfg,max(.2,args.sample_every))
        return 0

    try:
        SeaExplorerBot(cfg,dry_run=args.dry_run).run()
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
