"""Perception tracks -> predictive plan -> proportional held-touch control."""

from __future__ import annotations

import math
import threading
import time
from collections import deque

import cv2

from predictive_planner import InterceptPlanner, ObjectTracker


class TargetController:
    """Guide the diver to a planned X, releasing on stale frames or in deadband."""

    def __init__(self, touch, cfg):
        self.touch = touch
        self.cfg = cfg
        self.control_y = float(cfg["motion"].get("control_y", .67))
        self.tracker = ObjectTracker()
        self.planner = InterceptPlanner(cfg)
        self.active = False
        self.release_failed = False
        self.last_target = None
        self.last_plan = None
        self._goal = None
        self._mode = "IDLE"
        self._status = "IDLE"
        self._lock = threading.Lock()
        self._perception_lock = threading.Lock()
        self._stop = threading.Event()
        self._worker = None
        self._error = None
        self._last_observation = 0.0
        self._frame_size = None
        self._plan_at = None
        self.avoidance_decisions = 0
        self.collection_decisions = 0
        self.direction_changes = 0
        self.commanded_distance_px = 0
        self.major_reversals = 0
        self.edge_observations = 0
        self.observations = 0
        self.full_width_traversals = 0
        self._traversal_start = None
        self._previous_direction = 0
        self._move_latency_ms = deque(maxlen=2048)
        self._visual_candidate = None
        self._visual_candidate_count = 0
        self.tracked_objects_created = 0
        self.reachable_item_decisions = 0
        self.virus_tracks_created = 0
        self.direction_events = deque(maxlen=1200)
        self.target_events = deque(maxlen=1200)
        self._last_player_x = None
        self._player_observed_at = 0.0
        self._last_player_delta = 0
        self._last_goal_change_at = 0.0
        self._last_target_change_at = 0.0
        self._last_avoid_at = 0.0
        self._previous_goal = None
        self._filtered_goal = None
        self._filtered_at = None
        self._visual_at = None
        self._visual_velocity = 0.0
        self._mode_time = {"COLLECT":0.0,"EVADE":0.0,"RECOVER":0.0,"HOLD":0.0}
        self._previous_mode = None
        self._previous_mode_at = None
        self._most_recent_target_id = None

    def _deadband(self, width):
        return max(5, .018*width)

    def _motion_loop(self):
        try:
            while not self._stop.is_set():
                with self._lock:
                    goal, mode = self._goal, self._mode
                    size, observed = self._frame_size, self._last_observation
                    current, planned_at = self.last_target, self._plan_at
                if goal is None or size is None or current is None:
                    self._stop.wait(.02)
                    continue
                if time.monotonic()-observed > .45:
                    with self._lock:
                        self._status = "STALE-FRAME released"
                    break
                width, height = size
                with self._lock:
                    player_x = self._last_player_x
                    player_observed_at = self._player_observed_at
                    previous_direction = self._previous_direction
                if player_x is None or time.monotonic()-player_observed_at > .18:
                    with self._lock:
                        self._status = "PLAYER-LOST released"
                    break
                dx = goal-player_x
                deadband = .025*width if mode != "EVADE" else self._deadband(width)
                if abs(dx) <= deadband:
                    with self._lock:
                        self._status = f"{mode}-SETTLED player={player_x} goal={goal}"
                    break
                direction = 1 if dx > 0 else -1
                if (mode != "EVADE" and previous_direction and
                        direction != previous_direction and abs(dx) < .10*width):
                    with self._lock:
                        self._status = f"{mode}-REVERSAL-SUPPRESSED player={player_x} goal={goal}"
                    break
                # Proportional near the goal, speed-limited far away. Each
                # segment keeps the same finger down and can be superseded by
                # the next observed frame before a long crossing completes.
                duration_ms = 34 if mode == "EVADE" else 40
                max_step = .9*width*duration_ms/max(350, int(self.cfg["motion"].get("sweep_ms", 600)))
                # The finger is an actuator, not a position estimate. A
                # relative touch move can send the diver farther than the
                # finger moved, so close the loop on the observed diver.
                step = min(abs(dx)*.35, max_step)
                if getattr(self.touch, "low_rate", False):
                    step = min(abs(dx)*.35, max(max_step, .09*width))
                step = max(1, step)
                x = round(max(.03*width,min(.97*width,
                          current[0]+math.copysign(step, dx))))
                if x == current[0]:
                    with self._lock:
                        self._status = f"{mode}-TOUCH-EDGE released"
                    break
                self.touch.move_to(x, current[1], duration_ms, width, height)
                with self._lock:
                    if planned_at is not None:
                        self._move_latency_ms.append((time.monotonic()-planned_at)*1000)
                        self._plan_at = None
                    if self._previous_direction and self._previous_direction != direction:
                        self.direction_changes += 1
                        distance_before_reverse=abs(current[0]-self._traversal_start) if self._traversal_start is not None else 0
                        major=distance_before_reverse>=.15*width
                        if major:
                            self.major_reversals += 1
                        plan=self.last_plan
                        since_target=time.monotonic()-self._last_target_change_at
                        since_avoid=time.monotonic()-self._last_avoid_at
                        if mode=="EVADE" or since_avoid<.25:
                            cause="virus_avoidance_or_recovery"
                        elif since_target<.25:
                            cause="target_switch"
                        elif self._previous_goal is not None and abs(goal-self._previous_goal)<.05*width:
                            cause="overshoot_or_player_lag"
                        elif abs(self._last_player_delta)>.12*width:
                            cause="player_localization_jump"
                        elif since_target<.6:
                            cause="recent_target_switch"
                        else:
                            cause="goal_jitter_or_other"
                        self.direction_events.append({
                            "at":round(time.time(),3),"major":major,
                            "player_x":self._last_player_x,"touch_x":current[0],
                            "touch_player_gap_px":round(current[0]-self._last_player_x,1),
                            "previous_goal":self._previous_goal,"goal":goal,
                            "previous_direction":self._previous_direction,
                            "new_direction":direction,"target_id":plan.target_id if plan else None,
                            "target_score":self.planner.target_score,
                            "mode":mode,"threat":plan.threat if plan else 0,
                            "cause":cause,"reason":plan.reason if plan else None,
                            "distance_before_reverse_px":round(distance_before_reverse,1),
                        })
                        self._traversal_start = current[0]
                    elif self._traversal_start is None:
                        self._traversal_start = current[0]
                    if abs(x-self._traversal_start) >= .65*width:
                        self.full_width_traversals += 1
                        self._traversal_start = x
                    self._previous_direction = direction
                    self.commanded_distance_px += abs(x-current[0])
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

    def step(self, frame, hazards, dry_run=False, items=(), player_x=None, now=None):
        height, width = frame.shape[:2]
        now = time.monotonic() if now is None else now
        with self._lock:
            if self._error is not None:
                raise RuntimeError(f"Held-touch movement failed: {self._error}") from self._error
            current_x = self.last_target[0] if self.last_target else width//2
            finger_active=self.active
        visually_located = player_x is not None
        visual_confirmed = False
        if player_x is None:
            player_x = current_x
        elif abs(player_x-current_x) > .25*width:
            # One spurious visual blob cannot jump the control model, but two
            # consistent frames can correct a genuine input/model mismatch.
            if (self._visual_candidate is not None and
                    abs(player_x-self._visual_candidate)<.08*width):
                self._visual_candidate_count += 1
            else:
                self._visual_candidate_count = 1
            self._visual_candidate = player_x
            if self._visual_candidate_count < 2:
                player_x = current_x
            else:
                visual_confirmed = True
        else:
            self._visual_candidate = None
            self._visual_candidate_count = 0
        if (visually_located and not finger_active and
                (abs(player_x-current_x)<=.25*width or visual_confirmed)):
            current_x=round(player_x)
            with self._lock:
                self.last_target=(current_x,round(self.control_y*height))
        with self._perception_lock:
            prior_ids=set(self.tracker.tracks)
            tracks = self.tracker.update(items, hazards, now, width, height)
            created=[track for track in tracks if track.id not in prior_ids]
            self.tracked_objects_created += len(created)
            self.virus_tracks_created += sum(track.kind=="virus" for track in created)
            plan = self.planner.plan(tracks, player_x, width, height, now,
                                     movement_direction=self._previous_direction)
            self.reachable_item_decisions += plan.reachable
        previous_plan = self.last_plan
        if plan.mode=="EVADE":
            filtered_goal=plan.goal
        elif (plan.mode=="COLLECT" and previous_plan is not None and
              previous_plan.target_id==plan.target_id and
              self._filtered_goal is not None and self._filtered_at is not None):
            dt=max(.001,min(.15,now-self._filtered_at))
            difference=plan.goal-self._filtered_goal
            if abs(difference)<.018*width:
                filtered_goal=self._filtered_goal
            else:
                alpha=1-math.exp(-dt/.08)
                filtered_goal=self._filtered_goal+math.copysign(
                    min(abs(difference)*alpha,2.1*width*dt),difference)
        else:
            filtered_goal=plan.goal
        self._filtered_goal=filtered_goal
        self._filtered_at=now
        effective_goal=round(max(.04*width,min(.96*width,filtered_goal)))
        target = self.last_target or (current_x, round(self.control_y*height))
        with self._lock:
            previous_plan=self.last_plan
            previous_goal=self._goal
            self._last_player_delta=(player_x-self._last_player_x) if self._last_player_x is not None else 0
            self._last_player_x=player_x
            if visually_located:
                self._player_observed_at=now
            if previous_goal is not None and previous_goal!=effective_goal:
                self._previous_goal=previous_goal
                self._last_goal_change_at=time.monotonic()
            if previous_plan is not None and previous_plan.target_id!=plan.target_id:
                self._last_target_change_at=time.monotonic()
                self.target_events.append({
                    "at":round(time.time(),3),"old_target":previous_plan.target_id,
                    "new_target":plan.target_id,"old_mode":previous_plan.mode,
                    "new_mode":plan.mode,"old_goal":previous_goal,"new_goal":plan.goal,
                    "player_x":player_x,"reason":plan.reason,
                    "threat":plan.threat,"score":self.planner.target_score,
                    "previous_committed_target":self._most_recent_target_id,
                })
            if plan.target_id is not None:
                self._most_recent_target_id=plan.target_id
            if plan.mode=="EVADE":
                self._last_avoid_at=time.monotonic()
            if self._previous_mode is not None and self._previous_mode_at is not None:
                dt=max(0,min(.2,now-self._previous_mode_at))
                self._mode_time[self._previous_mode]=self._mode_time.get(self._previous_mode,0)+dt
            self._previous_mode=plan.mode
            self._previous_mode_at=now
            self._goal = effective_goal
            self._mode = plan.mode
            self._frame_size = (width, height)
            self._last_observation = now
            self._plan_at = time.monotonic()
            self.last_plan = plan
            self.observations += 1
            if player_x < .10*width or player_x > .90*width:
                self.edge_observations += 1
            if plan.mode == "EVADE":
                self.avoidance_decisions += 1
            elif plan.mode == "COLLECT":
                self.collection_decisions += 1
            error = effective_goal-player_x
            direction = "R" if error > self._deadband(width) else "L" if error < -self._deadband(width) else "-"
            self._status = (f"{plan.mode} target={plan.target_id or '-'} intercept={plan.intercept_x} "
                            f"eta={plan.eta if plan.eta is not None else '-'} "
                            f"player={player_x} raw={plan.goal} filtered={filtered_goal:.0f} "
                            f"goal={effective_goal} error={error:+.0f} dir={direction} "
                            f"threat={plan.threat:.2f} exclusion={plan.exclusion} reason={plan.reason}")
            status = self._status
        if dry_run:
            return "DRY-"+status, target
        if (visually_located and abs(effective_goal-player_x) > .04*width and
                (self._worker is None or not self._worker.is_alive())):
            self._stop.clear()
            start = (current_x, round(self.control_y*height))
            self.touch.begin(*start, width, height)
            with self._lock:
                self.last_target = start
                self.active = True
            self._worker = threading.Thread(target=self._motion_loop,
                                            name="SeaExplorer-TargetControl", daemon=True)
            self._worker.start()
        return status, target

    def render_debug(self, frame):
        """Optional annotated copy; never part of the normal capture path."""
        out = frame.copy()
        height, width = out.shape[:2]
        row = round(self.control_y*height)
        cv2.line(out, (0,row), (width-1,row), (255,255,255), 1)
        with self._lock:
            plan, current = self.last_plan, self.last_target
        with self._perception_lock:
            tracks=tuple(self.tracker.tracks.values())
        for track in tracks:
            color = (0,220,255) if track.kind == "item" else (220,30,220)
            cv2.circle(out, (round(track.x),round(track.y)), round(track.radius), color, 2)
            cv2.putText(out, f"{track.kind[0]}{track.id}", (round(track.x),round(track.y)),
                        cv2.FONT_HERSHEY_SIMPLEX, .35, color, 1)
            if track.vy > 0:
                eta = max(0,(row-track.y)/track.vy)
                cv2.line(out, (round(track.x),round(track.y)),
                         (round(track.x+track.vx*eta),row), color, 1)
        if plan:
            for lo,hi in plan.exclusion:
                cv2.rectangle(out,(round(lo),row-5),(round(hi),row+5),(30,30,255),-1)
            cv2.circle(out,(plan.goal,row),5,(30,255,30),-1)
        if current:
            cv2.circle(out,current,4,(255,255,255),-1)
        return out

    def snapshot_metrics(self):
        with self._lock:
            latency = self._move_latency_ms
            major_by_cause={}
            for event in self.direction_events:
                if event["major"]:
                    major_by_cause[event["cause"]]=major_by_cause.get(event["cause"],0)+1
            switch_reasons={}
            for event in self.target_events:
                if (event["new_target"] is not None and
                        event["new_target"]!=event["previous_committed_target"] and
                        event["previous_committed_target"] is not None):
                    switch_reasons[event["reason"]]=switch_reasons.get(event["reason"],0)+1
            return {
                "tracked_objects_created": self.tracked_objects_created,
                "virus_tracks_created": self.virus_tracks_created,
                "reachable_item_decisions": self.reachable_item_decisions,
                "target_switches": self.planner.target_switches,
                "direction_changes": self.direction_changes,
                "major_reversals": self.major_reversals,
                "full_width_traversals": self.full_width_traversals,
                "commanded_horizontal_px": self.commanded_distance_px,
                "edge_observation_ratio": round(self.edge_observations/max(1,self.observations),3),
                "decision_to_move_ms_mean": round(sum(latency)/len(latency),1) if latency else None,
                "last_plan": None if self.last_plan is None else self.last_plan.__dict__,
                "mode_time_s":{key:round(value,2) for key,value in self._mode_time.items()},
                "major_reversal_causes":major_by_cause,
                "target_switch_reasons":switch_reasons,
                "direction_events":list(self.direction_events),
                "target_events":list(self.target_events),
            }

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
            self._previous_direction = 0
            self._worker = None
            self._visual_candidate = None
            self._visual_candidate_count = 0
            self._filtered_goal = None
            self._filtered_at = None
            self._visual_at = None
            self._visual_velocity = 0.0
            self._player_observed_at = 0.0
            self._last_player_x = None
            self._most_recent_target_id = None
            self._previous_mode = None
            self._previous_mode_at = None
        with self._perception_lock:
            self.tracker = ObjectTracker()
            self.planner.reset()
