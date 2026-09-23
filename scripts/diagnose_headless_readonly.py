"""Bounded no-touch crash isolation for live stream and OpenCV vision."""

import argparse
import faulthandler
import json
import os
import sys
import threading
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from headless_scrcpy import HeadlessScrcpy
from sea_explorer_bot import ADB,Vision,load_config


def main():
    faulthandler.enable(all_threads=True)
    parser=argparse.ArgumentParser()
    parser.add_argument("--seconds",type=float,default=20)
    parser.add_argument("--out",type=Path,required=True)
    args=parser.parse_args()
    if not 5<=args.seconds<=60:
        parser.error("seconds must be 5..60")
    cfg=load_config()
    adb=ADB(cfg.get("adb_path",""))
    adb.ensure_ready()
    runtime=HeadlessScrcpy(adb,cfg.get("scrcpy_path",""),max_size=720,max_fps=60)
    vision=Vision(cfg)
    result={"mode":"no-touch stream+vision","seconds_requested":args.seconds,
            "frames_processed":0,"states":{},"serial":adb.serial}
    def hard_stop():
        os._exit(124)
    watchdog=threading.Timer(args.seconds+15,hard_stop)
    watchdog.daemon=True
    watchdog.start()
    started=time.monotonic()
    try:
        runtime.start()
        while time.monotonic()-started<args.seconds:
            frame=runtime.capture(timeout=.6)
            hsv=vision._hsv(frame)
            hazards=vision.hazards(frame,hsv=hsv)
            vision.collectibles(frame,hsv=hsv)
            detection=vision.detect(frame,hsv=hsv,hazards_hint=hazards)
            result["frames_processed"]+=1
            result["states"][detection.state]=result["states"].get(detection.state,0)+1
            if result["frames_processed"]%60==0:
                print(f"frames={result['frames_processed']} state={detection.state}",flush=True)
        result["status"]="completed"
        result["elapsed_s"]=round(time.monotonic()-started,2)
        return 0
    except Exception as exc:
        result["status"]="failed"
        result["error"]=f"{type(exc).__name__}: {exc}"
        return 1
    finally:
        watchdog.cancel()
        runtime.close()
        args.out.parent.mkdir(parents=True,exist_ok=True)
        args.out.write_text(json.dumps(result,indent=2),encoding="utf-8")


if __name__=="__main__":
    raise SystemExit(main())
