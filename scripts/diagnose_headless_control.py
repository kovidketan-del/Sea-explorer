"""Bounded launcher-only concurrent video/touch crash isolation."""

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

from headless_scrcpy import HeadlessScrcpy,ScrcpySocketTouch
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
    adb.run("shell","input","keyevent","HOME",timeout=4)
    runtime=HeadlessScrcpy(adb,cfg.get("scrcpy_path",""),max_size=720,max_fps=60)
    touch=ScrcpySocketTouch(runtime)
    vision=Vision(cfg)
    stop=threading.Event()
    result={"mode":"launcher stream+concurrent held-touch","seconds_requested":args.seconds,
            "frames_processed":0,"moves_sent":0,"serial":adb.serial}
    watchdog=threading.Timer(args.seconds+15,lambda:os._exit(124))
    watchdog.daemon=True
    watchdog.start()
    worker=None
    started=time.monotonic()
    try:
        runtime.start()
        w,h=runtime.video_size
        def move():
            touch.begin(w//2,int(.67*h),w,h)
            try:
                while not stop.is_set():
                    for x in (int(.38*w),int(.62*w)):
                        if stop.is_set():
                            break
                        touch.move_to(x,int(.67*h),52,w,h)
                        result["moves_sent"]+=1
            finally:
                touch.release()
        worker=threading.Thread(target=move,daemon=True)
        worker.start()
        while time.monotonic()-started<args.seconds:
            frame=runtime.capture(timeout=.6)
            hsv=vision._hsv(frame)
            hazards=vision.hazards(frame,hsv=hsv)
            vision.collectibles(frame,hsv=hsv)
            vision.detect(frame,hsv=hsv,hazards_hint=hazards)
            result["frames_processed"]+=1
            if result["frames_processed"]%60==0:
                print(f"frames={result['frames_processed']} moves={result['moves_sent']}",flush=True)
        result["status"]="completed"
        return 0
    except Exception as exc:
        result["status"]="failed"
        result["error"]=f"{type(exc).__name__}: {exc}"
        return 1
    finally:
        stop.set()
        if worker is not None:
            worker.join(timeout=2)
        watchdog.cancel()
        try:
            touch.release()
        finally:
            runtime.close()
        result["elapsed_s"]=round(time.monotonic()-started,2)
        args.out.parent.mkdir(parents=True,exist_ok=True)
        args.out.write_text(json.dumps(result,indent=2),encoding="utf-8")


if __name__=="__main__":
    raise SystemExit(main())
