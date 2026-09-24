"""Bounded, phone-specific benchmark of headless encoded-video capture."""

import argparse
import json
import statistics
import sys
import threading
import time
from pathlib import Path

import cv2
import psutil

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from sea_explorer_bot import ADB,Vision,load_config
from headless_scrcpy import HeadlessScrcpy,ScrcpySocketTouch
from target_control import TargetController


def summary(samples,scale=1000):
    if not samples:
        return None
    samples=sorted(samples)
    return {"median_ms":round(statistics.median(samples)*scale,2),
            "p95_ms":round(samples[min(len(samples)-1,int(len(samples)*.95))]*scale,2),
            "max_ms":round(samples[-1]*scale,2)}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--codec",choices=("h264","h265"),default="h264")
    parser.add_argument("--max-fps",type=int,default=60)
    args=parser.parse_args()
    cfg=load_config()
    adb=ADB(cfg.get("adb_path",""));adb.ensure_ready()
    adb.run("shell","input","keyevent","HOME")
    runtime=HeadlessScrcpy(adb,cfg.get("scrcpy_path",""),max_size=720,
                          codec=args.codec,max_fps=args.max_fps)
    touch=ScrcpySocketTouch(runtime)
    own=psutil.Process()
    result={"backend":f"scrcpy 4.1 {args.codec} packets -> isolated FFmpeg BGR, no window"}
    try:
        runtime.start()
        w,h=runtime.video_size
        result["frame_size"]=[w,h]
        frame=runtime.capture()
        decoder=psutil.Process(runtime.decoder_process.pid)
        start_cpu={"parent":own.cpu_times(),"decoder":decoder.cpu_times()}
        start=time.perf_counter()
        done=threading.Event()
        def animate():
            try:
                touch.begin(w//2,int(.67*h),w,h)
                for x in (int(.7*w),int(.3*w),int(.7*w),int(.3*w)):
                    touch.move_to(x,int(.67*h),500,w,h)
            finally:
                touch.release()
                done.set()
        worker=threading.Thread(target=animate,daemon=True)
        worker.start()
        waits=[];last_sequence=runtime._sequence;changed=0
        while not done.is_set() or time.perf_counter()-start<2.1:
            if time.perf_counter()-start>4.0:
                break
            tick=time.perf_counter()
            runtime.capture(timeout=.3)
            waits.append(time.perf_counter()-tick)
            if runtime._sequence!=last_sequence:
                changed+=1;last_sequence=runtime._sequence
        worker.join(timeout=2)
        elapsed=time.perf_counter()-start
        end_cpu={"parent":own.cpu_times(),"decoder":decoder.cpu_times()}
        result["stream_fps"] = round(changed/elapsed,1)
        result["new_frame_wait"] = summary(waits)
        cpu={name:round(100*((end_cpu[name].user+end_cpu[name].system)
                             -(start_cpu[name].user+start_cpu[name].system))/elapsed,1)
             for name in start_cpu}
        result["cpu_pct_one_core"]=cpu|{"total":round(sum(cpu.values()),1)}
        result["decode"] = summary([x/1000 for x in runtime.metrics["decode_ms"]])
        result["bgr_conversion"] = summary([x/1000 for x in runtime.metrics["convert_ms"]])
        result["decode_stage_note"]="FFmpeg executable does not expose per-frame decode/conversion timestamps"
        result["packets"] = runtime.metrics["packets"]
        result["frames"] = runtime.metrics["frames"]

        responses=[]
        for _ in range(4):
            adb.run("shell","input","touchscreen","motionevent","UP",500,1608,check=False)
            time.sleep(.12)
            reference=runtime.capture()[:60].copy()
            t0=time.perf_counter()
            adb.run("shell","input","touchscreen","motionevent","DOWN",500,1608,timeout=3)
            t1=time.perf_counter()
            deadline=t0+1
            changed_at=None
            while time.perf_counter()<deadline:
                image=runtime.capture(timeout=.12)[:60]
                if cv2.absdiff(reference,image).mean()>2.5:
                    changed_at=time.perf_counter();break
            adb.run("shell","input","touchscreen","motionevent","UP",500,1608,timeout=3)
            if changed_at is not None:
                responses.append({"input_to_frame_ms":round((changed_at-t0)*1000,1),
                                  "after_adb_return_ms":round((changed_at-t1)*1000,1),
                                  "adb_dispatch_ms":round((t1-t0)*1000,1)})
        result["pointer_response"] = responses

        # The same overlay provides an input-to-visible-frame bound for the
        # actual low-latency control socket, without adb shell startup time.
        direct=[]
        for _ in range(4):
            touch.release()
            time.sleep(.12)
            reference=runtime.capture()[:60].copy()
            t0=time.perf_counter()
            touch.begin(w//2,int(.67*h),w,h)
            changed_at=None
            while time.perf_counter()-t0<.5:
                image=runtime.capture(timeout=.12)[:60]
                if cv2.absdiff(reference,image).mean()>2.5:
                    changed_at=time.perf_counter();break
            touch.release()
            if changed_at is not None:
                direct.append(round((changed_at-t0)*1000,1))
        result["socket_touch_to_frame_ms"]=direct

        fixture=cv2.imread(str(ROOT/"assets"/"regression"/"sea_48_5.jpg"))
        fixture=cv2.resize(fixture,(w,h))
        vision=Vision(cfg);control=TargetController(None,cfg)
        parts={"hsv":[],"hazards":[],"items":[],"state":[],"decision":[]}
        for _ in range(40):
            t=time.perf_counter();hsv=vision._hsv(fixture);parts["hsv"].append(time.perf_counter()-t)
            t=time.perf_counter();hazards=vision.hazards(fixture,hsv);parts["hazards"].append(time.perf_counter()-t)
            t=time.perf_counter();items=vision.collectibles(fixture,hsv);parts["items"].append(time.perf_counter()-t)
            t=time.perf_counter();vision.detect(fixture,hsv,hazards);parts["state"].append(time.perf_counter()-t)
            t=time.perf_counter();control._plan(w,h,hazards,items,w//2);parts["decision"].append(time.perf_counter()-t)
        result["processing"]={name:summary(values) for name,values in parts.items()}
        print(json.dumps(result,indent=2),flush=True)
    finally:
        try:
            touch.release()
            adb.run("shell","input","touchscreen","motionevent","UP",500,1608,check=False)
        finally:
            runtime.close()
            adb.run("shell","input","keyevent","BACK",check=False)


if __name__=="__main__":
    main()
