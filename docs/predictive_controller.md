# Predictive controller: recording evidence and verification status

The 20:14 user recording is the regression source. The visible diver holds near
the middle early in the dive and later travels most of the playfield width
between targets. The previous `target_control.py` chose from each individual
frame's current object X. Its target lock was only a weak bonus for a similar
X coordinate, and its motion worker sent fixed-sized segments until the new
goal was reached. It neither estimated item arrival time nor rejected
unreachable cross-screen chases. Emergency candidate lanes included both
screen edges. Those mechanisms explain the recorded behavior.

The replacement is deliberately split:

1. `Vision` detects items, viruses and the magenta diver bag. `ObjectTracker`
   associates detections for a short lifetime and smooths X/Y velocity.
2. `InterceptPlanner` estimates arrival at the diver's row, predicts X at
   arrival, checks reachability against horizontal speed and latency, rejects
   unsafe trajectories through virus exclusion zones, and holds a target ID
   until a materially better safe target appears. A low-value item needing
   more than 55% of screen width is skipped. Normal targets are kept at least
   12% of the screen width in from either edge; the diver's body can still
   overlap and collect objects near a boundary. Emergency avoidance may use
   more space when necessary.
3. `TargetController` moves one held touch in short, interruptible segments.
   Step size tapers with the remaining X error; within a 2.5%-width deadband
   it releases. A stale frame also releases. No randomness is injected.

The recording samples at 49.6, 49.8 and 50.0 seconds show a gold item
descending from about Y=156 to Y=397 in a 324x720 crop. The new regression
checks that its track ID persists, velocity is downward, and the predicted
intercept differs from the item's current X. A late-run 70-second crop checks
that player localization can recover even when the prior X is on the opposite
side. Synthetic cases cover unreachable loot, minor score changes, early virus
evasion and resuming collection afterward.

The replay script measures plans against recorded frames only. It does not
physically change the run, so its planned collection or avoidance decisions
are **not** evidence that those items would actually have been collected or
that viruses would have been avoided. The initial replay of the prior
recording found about 5,884 image pixels of observed horizontal diver travel
at 324-pixel width over the 46–73 second interval, with 14 hypothetical
target switches from the first predictive draft. A later replay was
interrupted externally and is not accepted as a result. The 55%-width limit,
edge margin and trajectory-window checks were added afterward. Bounded split
replays of the tuned planner then gave 0 of 98 goal samples in the outer 10%
of the screen in the 46–58 second segment, and 1 of 118 in the 58–72 second
segment. In that later segment, the original recorded diver trace travelled
about 4,280 image pixels, with 13 major reversals and 10 near-full-width
traversals. These are image-space measurements/counterfactual planner goals,
not live before/after outcome measurements. Device validation is still needed.

Run `python -m unittest discover` for deterministic regressions. The
`test_headless_scrcpy` suite also round-trips an in-memory H.264 stream through
the exact FFmpeg decoder command; this exposed `-fflags nobuffer` producing no
frames, so that option was removed.

Live acceptance remains pending while no Android device is listed by ADB.
When connected, use `scripts/bounded_live_headless.py --seconds 120 --out
<receipt.json> --snapshots-dir <directory> --debug-overlay` for bounded
no-purchase runs. Review the receipt, touch overlay snapshots, run results and
failure frames. Repeat enough times to measure actual item gains, collisions,
clearance, run duration, reversals, edge time, travel per successful item and
post-virus recovery. Offline tests do not establish a 95% avoidance rate or
bag-capacity utilization.
