"""Bounded baseline for the current visible scrcpy-window capture path.

Runs on the Android launcher; it never starts a game run or buys upgrades.
The POINTER LOCATION overlay must already be enabled on the device.
"""

import json
import statistics
import sys
import time
from pathlib import Path

import cv2
import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sea_explorer_bot import ADB, Vision, load_config
from scrcpy_runtime import ScrcpyRuntime
from target_control import TargetController


def _summary(samples):
    if not samples:
        return None
    ordered = sorted(samples)
    return {
        "median_ms": round(statistics.median(ordered) * 1000, 2),
        "p95_ms": round(ordered[min(len(ordered)-1, int(len(ordered)*.95))] * 1000, 2),
        "max_ms": round(ordered[-1] * 1000, 2),
    }


def main():
    cfg = load_config()
    adb = ADB(cfg.get("adb_path", ""))
    adb.ensure_ready()
    adb.run("shell", "input", "keyevent", "HOME")
    runtime = ScrcpyRuntime(adb, cfg.get("scrcpy_path", ""))
    results = {"backend": "scrcpy visible window + MSS + upscaled BGR", "samples": 0}
    try:
        runtime.start()
        process = psutil.Process(runtime.process.pid)
        before_cpu = process.cpu_times()
        start = time.perf_counter()
        capture_times = []
        frame = None
        for _ in range(90):
            tick = time.perf_counter()
            frame = runtime.capture()
            capture_times.append(time.perf_counter()-tick)
        elapsed = time.perf_counter()-start
        after_cpu = process.cpu_times()
        results["samples"] = len(capture_times)
        results["capture_call"] = _summary(capture_times)
        results["capture_calls_per_s"] = round(len(capture_times)/elapsed, 1)
        results["scrcpy_cpu_pct_one_core"] = round(
            100*((after_cpu.user+after_cpu.system)-(before_cpu.user+before_cpu.system))/elapsed, 1
        )
        results["frame_shape"] = frame.shape

        # Synthetic ADB input gives a host-clock bound on input-to-visible-frame
        # delay.  It includes ADB dispatch; it is NOT pure device-encode latency.
        observed = []
        for _ in range(4):
            adb.run("shell", "input", "touchscreen", "motionevent", "UP", 500, 1608, check=False)
            time.sleep(.12)
            reference = runtime.capture()[:150].copy()
            t0 = time.perf_counter()
            adb.run("shell", "input", "touchscreen", "motionevent", "DOWN", 500, 1608, timeout=3)
            t1 = time.perf_counter()
            deadline = t0 + 1.0
            changed = None
            while time.perf_counter() < deadline:
                image = runtime.capture()[:150]
                if cv2.absdiff(reference, image).mean() > 2.5:
                    changed = time.perf_counter()
                    break
            adb.run("shell", "input", "touchscreen", "motionevent", "UP", 500, 1608, timeout=3)
            if changed is not None:
                observed.append({"input_to_frame_ms": round((changed-t0)*1000,1),
                                 "after_adb_return_ms": round((changed-t1)*1000,1),
                                 "adb_dispatch_ms": round((t1-t0)*1000,1)})
        results["pointer_response"] = observed

        # The previous path upscales 324x720 video to native screen resolution
        # before the existing OpenCV vision and planner run.
        fixture = cv2.imread(str(ROOT / "assets" / "regression" / "sea_48_5.jpg"))
        fixture = cv2.resize(fixture, (frame.shape[1], frame.shape[0]))
        vision = Vision(cfg)
        control = TargetController(None, cfg)
        parts = {"hsv": [], "hazards": [], "items": [], "state": [], "decision": []}
        for _ in range(40):
            t = time.perf_counter(); hsv=vision._hsv(fixture); parts["hsv"].append(time.perf_counter()-t)
            t = time.perf_counter(); hazards=vision.hazards(fixture,hsv); parts["hazards"].append(time.perf_counter()-t)
            t = time.perf_counter(); items=vision.collectibles(fixture,hsv); parts["items"].append(time.perf_counter()-t)
            t = time.perf_counter(); vision.detect(fixture,hsv,hazards); parts["state"].append(time.perf_counter()-t)
            t = time.perf_counter(); control._plan(fixture.shape[1],fixture.shape[0],hazards,items,fixture.shape[1]//2); parts["decision"].append(time.perf_counter()-t)
        results["processing"] = {name:_summary(times) for name,times in parts.items()}
        print(json.dumps(results, indent=2), flush=True)
    finally:
        try:
            adb.run("shell", "input", "touchscreen", "motionevent", "UP", 500, 1608, check=False)
        finally:
            runtime.close()
            adb.run("shell", "input", "keyevent", "BACK", check=False)


if __name__ == "__main__":
    main()
