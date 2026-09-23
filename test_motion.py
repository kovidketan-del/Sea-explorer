import threading
import time
import unittest

import numpy as np

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


class TargetControlTests(unittest.TestCase):
    def setUp(self):
        self.touch = FakeTouch()
        self.cfg = load_config()
        self.controller = TargetController(self.touch, self.cfg)
        self.frame = np.zeros((2400, 1080, 3), np.uint8)

    def tearDown(self):
        self.controller.stop()

    def test_no_item_does_not_sweep(self):
        reason, _ = self.controller.step(self.frame, (), dry_run=True)
        self.assertIn("DRY-HOLD", reason)
        self.assertEqual(self.touch.actions, [])

    def test_visible_item_is_targeted_without_hazard(self):
        goal, mode, _ = self.controller._plan(1080, 2400, (), ((825, 1150, 70),), 540)
        self.assertEqual((goal, mode), (825, "COLLECT"))

    def test_virus_blocks_item_and_forces_early_escape(self):
        hazard = ((540, 900, 170),)
        goal, mode, _ = self.controller._plan(1080, 2400, hazard, ((540, 1050, 55),), 540)
        self.assertEqual(mode, "EVADE")
        self.assertGreater(abs(goal - 540), 170)
        self.assertGreater(self.controller._clearance(goal, hazard, 1080), 0)

    def test_survival_takes_precedence_over_visible_gold(self):
        hazard = ((830, 950, 160),)
        goal, mode, _ = self.controller._plan(1080, 2400, hazard, ((825, 1200, 60),), 540)
        self.assertEqual((goal, mode), (540, "HOLD"))

    def test_one_hold_moves_toward_target_without_alternating(self):
        self.controller.step(self.frame, (), items=((860, 1100, 70),))
        deadline = time.monotonic() + .6
        moves = []
        while time.monotonic() < deadline:
            with self.touch.lock:
                moves = [a for a in self.touch.actions if a[0] == "MOVE"]
            if len(moves) >= 4:
                break
            time.sleep(.005)
        self.assertGreaterEqual(len(moves), 4)
        self.assertEqual(self.touch.actions[0][0], "DOWN")
        self.assertEqual([m[1] for m in moves[:4]], sorted(m[1] for m in moves[:4]))
        self.assertEqual(self.controller.direction_changes, 0)
        self.controller.stop()
        self.assertEqual(sum(a[0] == "UP" for a in self.touch.actions), 1)


if __name__ == "__main__":
    unittest.main()
