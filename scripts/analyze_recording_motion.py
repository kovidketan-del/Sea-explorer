"""Read-only trajectory sample from the user's desktop gameplay recording.

The old recording includes a scrcpy window at a fixed desktop position. This
script extracts only that window and prints detector coordinates over time; it
does not control the phone or change game state.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sea_explorer_bot import Vision, load_config


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("video", type=Path)
    parser.add_argument("--start", type=float, default=47)
    parser.add_argument("--end", type=float, default=73)
    parser.add_argument("--step", type=float, default=.5)
    parser.add_argument("--sheet", type=Path)
    parser.add_argument("--frames-dir", type=Path)
    args = parser.parse_args()
    cap = cv2.VideoCapture(str(args.video))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open {args.video}")
    vision = Vision(load_config())
    panels = []
    for t in np.arange(args.start, args.end, args.step):
        cap.set(cv2.CAP_PROP_POS_MSEC, float(t * 1000))
        ok, frame = cap.read()
        if not ok:
            break
        game = cv2.resize(frame[85:875, 1035:1451], (720, 1600))
        items = vision.collectibles(game)
        hazards = vision.hazards(game)
        print(f"{t:.2f} items={items} hazards={hazards}")
        if args.frames_dir:
            args.frames_dir.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(args.frames_dir / f"recording_{int(round(t*10)):04d}.jpg"),
                        cv2.resize(game, (324,720)), [cv2.IMWRITE_JPEG_QUALITY, 85])
        if args.sheet:
            thumb = cv2.resize(game, (180, 400))
            cv2.putText(thumb, f"{t:.1f}s", (5, 25), cv2.FONT_HERSHEY_SIMPLEX,
                        .6, (255, 255, 255), 2)
            panels.append(thumb)
    if args.sheet and panels:
        args.sheet.parent.mkdir(parents=True, exist_ok=True)
        width = 5
        rows = []
        for i in range(0, len(panels), width):
            chunk = panels[i:i+width]
            chunk += [np.zeros_like(panels[0]) for _ in range(width-len(chunk))]
            rows.append(np.concatenate(chunk, axis=1))
        cv2.imwrite(str(args.sheet), np.concatenate(rows, axis=0))


if __name__ == "__main__":
    main()
