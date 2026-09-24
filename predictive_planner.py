"""Short-lived visual tracks and reachability-aware interception planning.

Coordinates are in the decoded phone frame. Objects descend toward the diver's
collection row; the planner never steers directly from a raw detection.
"""

from __future__ import annotations

from dataclasses import dataclass


def clamp(value, low, high):
    return max(low, min(high, value))


@dataclass
class Track:
    id: int
    kind: str
    x: float
    y: float
    radius: float
    vx: float
    vy: float
    seen_at: float
    observations: int = 1

    def at(self, when):
        dt = max(0.0, when - self.seen_at)
        return self.x + self.vx * dt, self.y + self.vy * dt


class ObjectTracker:
    def __init__(self):
        self.tracks = {}
        self.next_id = 1

    def update(self, items, viruses, now, width, height):
        for kind, detections in (("item", items), ("virus", viruses)):
            unmatched = {track.id for track in self.tracks.values() if track.kind == kind}
            for x, y, radius in detections:
                choices = []
                for track_id in unmatched:
                    track = self.tracks[track_id]
                    dt = max(.001, now - track.seen_at)
                    px, py = track.at(now)
                    dx, dy = abs(x-px), abs(y-py)
                    if dx <= .19*width + abs(track.vx)*dt and dy <= .04*height + 1.5*height*dt:
                        choices.append((dx/width + dy/height, track_id))
                if choices:
                    _, track_id = min(choices)
                    unmatched.remove(track_id)
                    track = self.tracks[track_id]
                    dt = max(.001, now-track.seen_at)
                    if .012 <= dt <= .35:
                        measured_vx = clamp((x-track.x)/dt, -.8*width, .8*width)
                        measured_vy = clamp((y-track.y)/dt, -.2*height, 1.6*height)
                        alpha = .45 if track.observations > 1 else .7
                        track.vx = (1-alpha)*track.vx + alpha*measured_vx
                        track.vy = (1-alpha)*track.vy + alpha*measured_vy
                    track.x = .25*track.x + .75*x
                    track.y = .25*track.y + .75*y
                    track.radius = .4*track.radius + .6*radius
                    track.seen_at = now
                    track.observations += 1
                else:
                    track_id = self.next_id
                    self.next_id += 1
                    self.tracks[track_id] = Track(track_id, kind, float(x), float(y),
                                                   float(radius), 0.0, .70*height, now)
        self.tracks = {key: track for key, track in self.tracks.items()
                       if now-track.seen_at <= .28}
        return tuple(self.tracks.values())


@dataclass(frozen=True)
class Plan:
    goal: int
    mode: str
    target_id: int | None
    intercept_x: float | None
    eta: float | None
    reason: str
    exclusion: tuple[tuple[float, float], ...]
    threat: float
    reachable: int


class InterceptPlanner:
    def __init__(self, cfg):
        motion = cfg["motion"]
        self.row_ratio = float(motion.get("control_y", .67))
        self.speed_ratio = .9/max(.35, float(motion.get("sweep_ms", 600))/1000)
        self.target_id = None
        self.target_score = None
        self.target_switches = 0
        self.switch_reason = ""

    def reset(self):
        self.target_id = None
        self.target_score = None
        self.switch_reason = ""

    def plan(self, tracks, player_x, width, height, now):
        row = self.row_ratio*height
        left, right = .06*width, .94*width
        player_x = clamp(player_x, left, right)
        speed = self.speed_ratio*width
        latency = .07
        exclusions = []
        viruses = []
        for track in tracks:
            if track.kind != "virus" or now-track.seen_at > .20:
                continue
            x, y = track.at(now)
            vy = max(.35*height, track.vy)
            eta = (row-y)/vy
            if eta < -.10 or eta > 1.1:
                continue
            impact_x = clamp(x+track.vx*max(0, eta), left, right)
            # Width includes both bodies, timing/vision uncertainty and a
            # small reserve for the next frame's reaction time.
            margin = track.radius + .065*width + .035*width + abs(track.vx)*latency
            interval = (max(left, impact_x-margin), min(right, impact_x+margin))
            exclusions.append(interval)
            viruses.append((track, eta, impact_x, margin))

        def predicted_player(goal, eta):
            available = max(0, eta-latency)
            return player_x + clamp(goal-player_x, -speed*available, speed*available)

        def safe(goal):
            for track, eta, vx, margin in viruses:
                vy = max(.35*height, track.vy)
                half_window = min(.15,(track.radius+.055*height)/vy)
                for when in (max(0,eta-half_window), max(0,eta),
                             max(0,eta+half_window)):
                    virus_x = vx+track.vx*(when-max(0,eta))
                    if abs(predicted_player(goal, when)-virus_x) < margin:
                        return False
            return True

        current_safe = safe(player_x)
        threat = 0.0
        for _track, eta, vx, margin in viruses:
            clearance = abs(player_x-vx)-margin
            if clearance < .10*width:
                threat = max(threat, clamp((.10*width-clearance)/(.20*width), 0, 1))
        if not current_safe:
            candidates = [clamp(player_x, .12*width, .88*width), .12*width, .88*width]
            for _track, _eta, vx, margin in viruses:
                candidates.extend((clamp(vx-margin-.018*width, left, right),
                                   clamp(vx+margin+.018*width, left, right)))
            clear = [x for x in candidates if safe(x)]
            if clear:
                goal = min(clear, key=lambda x: abs(x-player_x)+.16*abs(x-.5*width))
                reason = "predicted-virus-crossing"
            else:
                # With no fully clear reachable lane, maximize impact
                # separation. Do not force an edge unless it is safer.
                def clearance(x):
                    return min((abs(predicted_player(x, eta)-vx)-margin
                                for _t, eta, vx, margin in viruses), default=0)
                goal = max(candidates, key=lambda x: clearance(x)-.03*abs(x-.5*width))
                reason = "emergency-best-clearance"
            self.reset()
            return Plan(round(goal), "EVADE", None, None, None, reason,
                        tuple(exclusions), threat, 0)

        candidates = []
        for track in tracks:
            if track.kind != "item" or now-track.seen_at > .16:
                continue
            x, y = track.at(now)
            if not .15*height <= y < row-.012*height:
                continue
            vy = max(.40*height, track.vy)
            eta = (row-y)/vy
            if eta > 1.2 or eta < .07:
                continue
            # The diver has width: collecting an edge item rarely requires
            # pinning the control point against the actual display boundary.
            intercept = clamp(x+track.vx*eta, .12*width, .88*width)
            distance = abs(intercept-player_x)
            if distance > .55*width and track.radius < .11*width:
                continue
            if distance/speed + latency > eta+.035:
                continue
            if not safe(intercept):
                continue
            # Value is a deliberately weak size proxy; route cost and safety
            # dominate so distant items cannot provoke needless traversals.
            value = 1 + .12*clamp(track.radius/(.07*width), .4, 1.7)
            score = value - .85*distance/width - .10*abs(intercept-.5*width)/width
            score += .10*clamp((.75-eta)/.75, 0, 1)
            candidates.append((score, track, intercept, eta))
        candidates.sort(key=lambda row: row[0], reverse=True)
        reachable = len(candidates)
        chosen = candidates[0] if candidates else None
        previous = next((entry for entry in candidates if entry[1].id == self.target_id), None)
        if previous is not None and chosen is not None and chosen[1].id != self.target_id:
            # A slight score fluctuation must not reverse a crossing.
            if chosen[0] < previous[0]+.18:
                chosen = previous
            else:
                self.switch_reason = "substantially-better-reachable-target"
        if chosen is not None:
            score, track, intercept, eta = chosen
            if self.target_id != track.id:
                if self.target_id is not None:
                    self.target_switches += 1
                if not self.switch_reason:
                    self.switch_reason = "new-reachable-target" if self.target_id is None else "old-target-lost-or-unsafe"
            else:
                self.switch_reason = "locked"
            self.target_id = track.id
            self.target_score = score
            return Plan(round(intercept), "COLLECT", track.id, intercept, eta,
                        self.switch_reason, tuple(exclusions), threat, reachable)

        self.reset()
        # Only drift inward if already pressed close to a boundary. Stay in a
        # useful lane otherwise; never oscillate around the exact center.
        goal = player_x
        if player_x < .14*width:
            goal = .20*width
        elif player_x > .86*width:
            goal = .80*width
        if not safe(goal):
            goal = player_x
        return Plan(round(goal), "HOLD", None, None, None,
                    "no-safe-reachable-item", tuple(exclusions), threat, reachable)
