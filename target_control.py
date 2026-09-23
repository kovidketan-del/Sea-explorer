"""Target-led, hazard-first steering for a continuously held touch."""

from __future__ import annotations

import math
import threading
import time


def _clamp(value, low, high):
    return max(low, min(high, value))


class TargetController:
    """Keep one finger down and revise a short smooth trajectory each frame.

    Observations are never turned into unconditional left/right sweeps.  If the
    camera stops producing fresh frames, lift the finger instead of continuing
    an unobserved movement.  Touch implementations may be scrcpy (fast smooth
    interpolation) or Android motionevent (slower but still one held pointer).
    """

    def __init__(self, touch, cfg):
        self.touch = touch
        self.cfg = cfg
        self.control_y = float(cfg["motion"].get("control_y", .67))
        self.active = False
        self.release_failed = False
        self.last_target = None
        self._goal = None
        self._mode = "IDLE"
        self._status = "IDLE"
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._worker = None
        self._error = None
        self._last_observation = 0.0
        self._frame_size = None
        self._item_lock = None
        self.avoidance_decisions = 0
        self.collection_decisions = 0
        self.direction_changes = 0
        self._previous_direction = 0

    def _limits(self, w):
        m = self.cfg["motion"]
        return int(w * float(m.get("x_left", .05))), int(w * float(m.get("x_right", .95)))

    def _clearance(self, x, hazards, w):
        if not hazards:
            return float("inf")
        margin = .045 * w
        return min(abs(x - hx) - radius - margin for hx, _, radius in hazards)

    def _route_clear(self, start_x, end_x, hazards, w, h):
        if start_x == end_x:
            return True
        lo, hi = sorted((start_x, end_x))
        for hx, hy, radius in hazards:
            if hy < self.control_y*h - .48*h:
                continue
            if lo - radius - .045*w <= hx <= hi + radius + .045*w:
                return False
        return True

    def _plan(self, w, h, hazards, items, current_x):
        left, right = self._limits(w)
        row = self.control_y * h
        # A virus can cover the remaining vertical gap in well under a second.
        # Plan against it while it is still above the diver, not at contact.
        ahead = float(self.cfg["motion"].get("danger_ahead_ratio", .58)) * h
        threats = tuple(v for v in hazards if row - ahead <= v[1] <= row + .075 * h)
        current_x = int(_clamp(current_x, left, right))
        current_clearance = self._clearance(current_x, threats, w)

        safe_items = []
        for x, y, radius in items:
            if not (.17 * h <= y < row + .015 * h):
                continue
            # The diver's own gold bag overlaps the playfield just above the
            # collection line.  It is not falling loot and must not lock us
            # onto our current position forever.
            if y > .60 * h and abs(x - current_x) < .23 * w:
                continue
            x = int(_clamp(x, left, right))
            clearance = self._clearance(x, threats, w)
            if clearance < 0:
                continue
            if current_clearance >= 0 and not self._route_clear(current_x, x, threats, w, h):
                continue
            # Approaching objects matter more than distant ones, but avoid a
            # full-screen chase for an item already at the collection line.
            remaining = max(0, row - y)
            travel = abs(x - current_x) / w
            score = y / h - .32 * travel + min(radius / w, .1) * .12
            if remaining < .045 * h and travel > .20:
                score -= .35
            if self._item_lock is not None and abs(x - self._item_lock) < .07 * w:
                score += .055
            safe_items.append((score, x, y))

        if current_clearance < 0:
            # Escape to the nearest genuinely clear lane.  Edge-only parking
            # wastes almost every subsequent object and is deliberately avoided.
            candidates = {left, right, current_x}
            for hx, _, radius in threats:
                reach = radius + .065 * w
                candidates.add(int(_clamp(hx - reach, left, right)))
                candidates.add(int(_clamp(hx + reach, left, right)))
            candidates.update(x for _, x, _ in safe_items)
            safe = [x for x in candidates if self._clearance(x, threats, w) > .01 * w]
            if safe:
                # A nearby loot lane is useful only after safety is secured.
                goal = min(safe, key=lambda x: abs(x-current_x) - min(self._clearance(x, threats, w), .18*w)*.18)
            else:
                goal = max(candidates, key=lambda x: self._clearance(x, threats, w))
            return goal, "EVADE", threats

        if safe_items:
            _, goal, _ = max(safe_items)
            return goal, "COLLECT", threats

        # No visible safe loot: stay in a clear lane and wait for a useful
        # observation.  Repetitive left/right motion has no information value.
        return current_x, "HOLD", threats

    def _motion_loop(self):
        try:
            while not self._stop.is_set():
                with self._lock:
                    goal = self._goal
                    mode = self._mode
                    size = self._frame_size
                    observed = self._last_observation
                    current = self.last_target
                if goal is None or size is None or current is None:
                    self._stop.wait(.025)
                    continue
                if time.monotonic() - observed > .65:
                    with self._lock:
                        self._status = "STALE-FRAME; released"
                    break
                w, h = size
                dx = goal - current[0]
                if abs(dx) <= max(5, .008*w):
                    with self._lock:
                        self._status = f"{mode}-HOLD x={current[0]}"
                    self._stop.wait(.025)
                    continue
                # Short segments allow new hazard observations to interrupt
                # a chase.  Scrcpy interpolates each segment at 10-16 ms.
                chunk_ms = 48 if mode == "EVADE" else 52
                travel_ms = max(420, int(self.cfg["motion"].get("sweep_ms", 650)))
                step = max(14, int(w * .9 * chunk_ms / travel_ms))
                if getattr(self.touch, "low_rate", False):
                    step = max(step, int(w * (.23 if mode == "EVADE" else .16)))
                x = int(current[0] + math.copysign(min(abs(dx), step), dx))
                self.touch.move_to(x, current[1], chunk_ms, w, h)
                direction = 1 if dx > 0 else -1
                with self._lock:
                    if self._previous_direction and self._previous_direction != direction:
                        self.direction_changes += 1
                    self._previous_direction = direction
                    self.last_target = (x, current[1])
                    self._status = f"{mode} x={x} goal={goal}"
        except Exception as exc:
            with self._lock:
                self._error = exc
                self._status = f"MOTION-ERROR {type(exc).__name__}: {exc}"
        finally:
            try:
                if self.touch is not None:
                    self.touch.release()
            except Exception:
                self.release_failed = True
            with self._lock:
                self.active = False

    def step(self, frame, hazards, dry_run=False, items=()):
        h, w = frame.shape[:2]
        with self._lock:
            if self._error is not None:
                raise RuntimeError(f"Held-touch movement failed: {self._error}") from self._error
            current_x = self.last_target[0] if self.last_target else w // 2
            goal, mode, threats = self._plan(w, h, hazards, items, current_x)
            self._goal = goal
            self._mode = mode
            self._frame_size = (w, h)
            self._last_observation = time.monotonic()
            self._item_lock = goal if mode == "COLLECT" else None
            if mode == "EVADE":
                self.avoidance_decisions += 1
            elif mode == "COLLECT":
                self.collection_decisions += 1
            status = self._status
            target = self.last_target or (w // 2, int(self.control_y*h))

        if dry_run:
            return f"DRY-{mode} goal={goal} hazards={len(threats)} items={len(items)}", target
        if self._worker is None or not self._worker.is_alive():
            self._stop.clear()
            start = (current_x, int(self.control_y*h))
            self.touch.begin(*start, w, h)
            with self._lock:
                self.last_target = start
                self.active = True
            self._worker = threading.Thread(target=self._motion_loop, name="SeaExplorer-TargetControl", daemon=True)
            self._worker.start()
        return status, target

    def stop(self):
        self._stop.set()
        worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=2)
        if worker is not None and worker.is_alive():
            self.release_failed = True
            raise RuntimeError("Held-touch movement worker did not stop")
        try:
            if self.touch is not None:
                self.touch.release()
        except Exception:
            self.release_failed = True
            raise
        with self._lock:
            self.active = False
            self.last_target = None
            self._goal = None
            self._mode = "IDLE"
            self._status = "IDLE"
            self._frame_size = None
            self._item_lock = None
            self._previous_direction = 0
            self._worker = None
