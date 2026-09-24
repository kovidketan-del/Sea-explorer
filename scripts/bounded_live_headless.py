"""One bounded, no-purchase device run with a saved receipt and safe cleanup."""

import argparse
import faulthandler
import json
import os
import sys
import threading
import time
from pathlib import Path

import cv2

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from sea_explorer_bot import ADB,SeaExplorerBot,StopRequested,load_config


def main():
    faulthandler.enable(all_threads=True)
    parser=argparse.ArgumentParser()
    parser.add_argument("--seconds",type=float,default=75)
    parser.add_argument("--out",type=Path,required=True)
    parser.add_argument("--snapshots-dir",type=Path,
                        help="optional low-rate gameplay snapshots for visual review")
    parser.add_argument("--debug-overlay",action="store_true",
                        help="annotate optional snapshots with tracks, paths and exclusion zones")
    args=parser.parse_args()
    if not 5<=args.seconds<=180:
        parser.error("seconds must be 5..180")
    cfg=load_config()
    cfg["economy"]["buy_upgrades"]=False
    cfg["capture_backend"]="scrcpy_stream"
    cfg["control_backend"]="scrcpy_stream"
    adb=ADB(cfg.get("adb_path",""))
    adb.ensure_ready()
    if adb.is_keyguard_locked():
        raise RuntimeError("Android keyguard is showing; unlock the phone before a live gameplay test")
    adb.start_app("com.RayGaming.SeaExplorer")
    stop=threading.Event()
    bot=SeaExplorerBot(cfg,stop_event=stop,serial=adb.serial)
    runs_before=bot.progress.runs_completed
    snapshot_stop=threading.Event()
    snapshots=[]
    result={"serial":adb.serial,"seconds_requested":args.seconds,
            "backend":"scrcpy_stream","buy_upgrades":False}
    start=time.monotonic()

    def snapshot_loop():
        if args.snapshots_dir is None:
            return
        args.snapshots_dir.mkdir(parents=True,exist_ok=True)
        while not snapshot_stop.wait(3):
            runtime=bot.capture_runtime
            if runtime is None or runtime.last_frame_age>.5:
                continue
            frame=runtime.peek_frame()
            if frame is None:
                continue
            if args.debug_overlay:
                frame=bot.sweeper.render_debug(frame)
            path=args.snapshots_dir/f"frame_{int(time.monotonic()-start):04d}.jpg"
            if cv2.imwrite(str(path),frame,[cv2.IMWRITE_JPEG_QUALITY,85]):
                snapshots.append(str(path))

    snapshot_worker=threading.Thread(target=snapshot_loop,daemon=True)
    snapshot_worker.start()
    timer=threading.Timer(args.seconds,stop.set)
    timer.daemon=True
    timer.start()

    # If a device/API call violates its own timeout, release the finger and
    # stop the child. The outer completion supervisor will retain the nonzero
    # exit and its logs instead of waiting forever.
    def hard_stop():
        stop.set()
        try:
            bot.sweeper.stop()
        except Exception:
            pass
        try:
            if bot.capture_runtime is not None:
                bot.capture_runtime.close()
        except Exception:
            pass
        os._exit(124)

    watchdog=threading.Timer(args.seconds+20,hard_stop)
    watchdog.daemon=True
    watchdog.start()
    try:
        try:
            bot.run()
            result["status"]="ended_normally"
        except StopRequested:
            result["status"]="duration_reached"
        result["elapsed_s"]=round(time.monotonic()-start,2)
        result["runs_completed_total"]=bot.progress.runs_completed
        result["runs_completed_during_test"]=bot.progress.runs_completed-runs_before
        result["avoidance_decisions"]=bot.sweeper.avoidance_decisions
        result["collection_decisions"]=bot.sweeper.collection_decisions
        result["direction_changes"]=bot.sweeper.direction_changes
        result["movement"]=bot.sweeper.snapshot_metrics()
        result["release_failed"]=bot.sweeper.release_failed
        result["last_state"]=bot.prev_state
        result["gameplay_decisions"]=bot.sweeper.avoidance_decisions+bot.sweeper.collection_decisions
        result["success"]=(result["status"]=="duration_reached"
                           and not bot.sweeper.release_failed
                           and result["gameplay_decisions"]>0
                           and result["runs_completed_during_test"]>0)
        if result["status"]=="duration_reached" and not result["gameplay_decisions"]:
            result["status"]="no_gameplay_observed"
        elif result["status"]=="duration_reached" and not result["runs_completed_during_test"]:
            result["status"]="no_completed_run_observed"
        return 0 if result["success"] else 1
    except Exception as exc:
        result["status"]="failed"
        result["error"]=f"{type(exc).__name__}: {exc}"
        return 1
    finally:
        timer.cancel()
        watchdog.cancel()
        snapshot_stop.set()
        snapshot_worker.join(timeout=2)
        result.setdefault("elapsed_s",round(time.monotonic()-start,2))
        result.setdefault("runs_completed_during_test",bot.progress.runs_completed-runs_before)
        result.setdefault("avoidance_decisions",bot.sweeper.avoidance_decisions)
        result.setdefault("collection_decisions",bot.sweeper.collection_decisions)
        result.setdefault("direction_changes",bot.sweeper.direction_changes)
        result.setdefault("movement",bot.sweeper.snapshot_metrics())
        result.setdefault("release_failed",bot.sweeper.release_failed)
        result["stream"]=bot.stream_stats
        result["snapshot_count"]=len(snapshots)
        if args.snapshots_dir is not None:
            result["snapshots_dir"]=str(args.snapshots_dir)
        args.out.parent.mkdir(parents=True,exist_ok=True)
        args.out.write_text(json.dumps(result,indent=2),encoding="utf-8")
        try:
            adb.run("shell","input","keyevent","HOME",check=False,timeout=4)
        except Exception:
            pass


if __name__=="__main__":
    raise SystemExit(main())
