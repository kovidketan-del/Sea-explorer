"""Offline planner replay on the user's 20:14 recording; never touches Android.

Planned goals are counterfactual: they cannot prove collection or avoidance
outcomes without another live run. Actual diver travel comes from video pixels.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import cv2

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from sea_explorer_bot import Vision,load_config
from target_control import TargetController


def main():
    p=argparse.ArgumentParser()
    p.add_argument("video",type=Path)
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--start",type=float,default=46)
    p.add_argument("--end",type=float,default=73)
    p.add_argument("--step",type=float,default=.1)
    p.add_argument("--timeout",type=float,default=90)
    args=p.parse_args()
    cap=cv2.VideoCapture(str(args.video))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open {args.video}")
    vision=Vision(load_config())
    control=TargetController(None,load_config())
    prior=162
    observations=[]
    started=time.monotonic()
    count=int((args.end-args.start)/args.step)
    for i in range(count):
        if time.monotonic()-started>args.timeout:
            raise RuntimeError(f"Replay exceeded {args.timeout}s work timeout")
        t=args.start+i*args.step
        cap.set(cv2.CAP_PROP_POS_MSEC,t*1000)
        ok,frame=cap.read()
        if not ok:
            break
        game=cv2.resize(frame[85:875,1035:1451],(324,720))
        hsv=vision._hsv(game)
        if vision.detect(game,hsv=hsv).state!="PLAYING":
            continue
        player=vision.player_x(game,prior,hsv=hsv)
        if player is not None:
            prior=player
        items=vision.collectibles(game,hsv=hsv)
        viruses=vision.hazards(game,hsv=hsv)
        control.step(game,viruses,dry_run=True,items=items,player_x=prior,now=t)
        plan=control.last_plan
        observations.append({"t":round(t,2),"player_x":prior,
                             "player_observed":player is not None,
                             "items":len(items),"viruses":len(viruses),
                             "goal":plan.goal,"mode":plan.mode,
                             "target_id":plan.target_id,"reason":plan.reason,
                             "exclusion":plan.exclusion})
    width=324
    observed=[o for o in observations if o["player_observed"]]
    distances=[abs(b["player_x"]-a["player_x"])
               for a,b in zip(observed,observed[1:])]
    reversals=0
    full_width_traversals=0
    direction=0
    segment_start=observed[0]["player_x"] if observed else 0
    for a,b in zip(observed,observed[1:]):
        dx=b["player_x"]-a["player_x"]
        new_direction=1 if dx>2 else -1 if dx< -2 else 0
        if not new_direction:
            continue
        if direction and new_direction!=direction:
            if abs(a["player_x"]-segment_start)>.15*width:
                reversals+=1
            segment_start=a["player_x"]
        if abs(b["player_x"]-segment_start)>.65*width:
            full_width_traversals+=1
            segment_start=b["player_x"]
        direction=new_direction
    output={"recording":str(args.video),"disclaimer":"Planner replay is counterfactual; no live touch or collection proof.",
            "samples":len(observations),"visually_located_player_samples":len(observed),
            "actual_video_distance_px":sum(distances),
            "actual_video_major_reversals":reversals,
            "actual_video_full_width_traversals":full_width_traversals,
            "actual_video_edge_fraction":sum(o["player_x"]<.1*width or o["player_x"]>.9*width
                                              for o in observed)/max(1,len(observed)),
            "planned_edge_goal_fraction":sum(o["goal"]<.1*width or o["goal"]>.9*width
                                              for o in observations)/max(1,len(observations)),
            "planned_target_switches":control.planner.target_switches,
            "planned_collect_samples":sum(o["mode"]=="COLLECT" for o in observations),
            "planned_evade_samples":sum(o["mode"]=="EVADE" for o in observations),
            "observations":observations}
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(output,indent=2),encoding="utf-8")
    print(json.dumps({key:value for key,value in output.items() if key!="observations"},indent=2))


if __name__=="__main__":
    main()
