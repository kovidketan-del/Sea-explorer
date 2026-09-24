import threading
import time
import unittest

import numpy as np

from predictive_planner import InterceptPlanner, ObjectTracker, Track
from target_control import TargetController
from sea_explorer_bot import load_config


class FakeTouch:
    def __init__(self):
        self.actions = []
        self.active = False
        self.lock = threading.Lock()

    def begin(self, x, y, w, h):
        with self.lock:
            self.actions.append(("DOWN", x, y))
            self.active = True

    def move_to(self, x, y, duration_ms, w, h):
        with self.lock:
            self.actions.append(("MOVE", x, y, duration_ms))
        time.sleep(.001)

    def release(self):
        with self.lock:
            if self.active:
                self.actions.append(("UP",))
            self.active = False


class PredictivePlanningTests(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config()
        self.planner = InterceptPlanner(self.cfg)
        self.w, self.h = 720, 1600

    def track(self, ident, kind, x, y, vx=0, vy=1000, radius=45):
        return Track(ident, kind, x, y, radius, vx, vy, 1.0, 3)

    def test_tracker_estimates_downward_velocity_and_keeps_id(self):
        tracker = ObjectTracker()
        first = tracker.update(((200,400,40),), (), 1.0, self.w, self.h)[0]
        second = tracker.update(((210,505,40),), (), 1.1, self.w, self.h)[0]
        self.assertEqual(first.id, second.id)
        self.assertGreater(second.vy, 800)
        self.assertGreater(second.vx, 0)

    def test_intercepts_future_x_not_current_x(self):
        item = self.track(1, "item", 420, 650, vx=120, vy=900)
        plan = self.planner.plan((item,), 360, self.w, self.h, 1.0)
        self.assertEqual(plan.mode, "COLLECT")
        self.assertGreater(plan.goal, 450)
        self.assertLess(plan.goal, 520)

    def test_rejects_unreachable_cross_screen_chase(self):
        item = self.track(1, "item", 685, 980, vy=1100)
        plan = self.planner.plan((item,), 60, self.w, self.h, 1.0)
        self.assertEqual(plan.mode, "HOLD")
        self.assertLess(plan.goal, 200)

    def test_skips_low_value_opposite_edge_even_when_technically_reachable(self):
        item = self.track(1, "item", 680, 300, vy=700, radius=35)
        plan = self.planner.plan((item,), 60, self.w, self.h, 1.0)
        self.assertEqual(plan.mode, "HOLD")
        self.assertLess(plan.goal, 200)

    def test_prefers_reachable_nearby_item_over_distant_one(self):
        near = self.track(1, "item", 420, 600)
        far = self.track(2, "item", 670, 590)
        plan = self.planner.plan((far,near), 360, self.w, self.h, 1.0)
        self.assertEqual(plan.target_id, 1)

    def test_target_lock_resists_minor_score_change(self):
        a = self.track(1, "item", 300, 600)
        b = self.track(2, "item", 500, 600)
        first = self.planner.plan((a,b), 360, self.w, self.h, 1.0)
        self.assertEqual(first.target_id, 1)
        b.x = 450
        second = self.planner.plan((a,b), 360, self.w, self.h, 1.02)
        self.assertEqual(second.target_id, 1)
        self.assertEqual(self.planner.target_switches, 0)

    def test_predictive_virus_evasion_and_resume(self):
        virus = self.track(3, "virus", 360, 700, vy=1000, radius=65)
        item = self.track(1, "item", 345, 590)
        avoid = self.planner.plan((virus,item), 360, self.w, self.h, 1.0)
        self.assertEqual(avoid.mode, "EVADE")
        self.assertGreater(abs(avoid.goal-360), 90)
        self.assertTrue(avoid.exclusion)
        item.x=avoid.goal
        item.y=700
        item.seen_at=1.5
        after = self.planner.plan((item,), avoid.goal, self.w, self.h, 1.5)
        self.assertEqual(after.mode, "COLLECT")

    def test_far_virus_does_not_force_avoidance(self):
        virus = self.track(3, "virus", 650, 700, vy=1000, radius=65)
        plan = self.planner.plan((virus,), 360, self.w, self.h, 1.0)
        self.assertNotEqual(plan.mode, "EVADE")


class ProportionalControlTests(unittest.TestCase):
    def setUp(self):
        self.touch = FakeTouch()
        self.cfg = load_config()
        self.controller = TargetController(self.touch, self.cfg)
        self.frame = np.zeros((1600, 720, 3), np.uint8)

    def tearDown(self):
        self.controller.stop()

    def test_no_item_does_not_touch_phone(self):
        reason, _ = self.controller.step(self.frame, (), dry_run=False)
        self.assertIn("HOLD", reason)
        self.assertEqual(self.touch.actions, [])

    def test_short_move_settles_without_overshoot_or_reversal(self):
        self.controller.step(self.frame, (), items=((405,600,40),))
        deadline = time.monotonic()+.5
        while time.monotonic()<deadline and self.controller.active:
            time.sleep(.005)
        moves = [action for action in self.touch.actions if action[0]=="MOVE"]
        self.assertTrue(moves)
        self.assertLess(max(move[1] for move in moves), 415)
        self.assertEqual([move[1] for move in moves], sorted(move[1] for move in moves))
        self.assertEqual(self.controller.direction_changes, 0)
        self.assertEqual(sum(action[0]=="UP" for action in self.touch.actions),1)


if __name__ == "__main__":
    unittest.main()
