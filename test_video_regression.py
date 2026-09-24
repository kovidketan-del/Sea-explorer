"""Regression cases extracted from the user's 2026-09-23 gameplay recording."""

import unittest
from pathlib import Path

import cv2

from sea_explorer_bot import Vision, load_config
from target_control import TargetController


FRAMES = Path(__file__).parent / "assets" / "regression"


class RecordedGameplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = load_config()
        cls.vision = Vision(cls.cfg)
        cls.control = TargetController(None, cls.cfg)

    def frame(self, name):
        image = cv2.imread(str(FRAMES / name))
        self.assertIsNotNone(image)
        return image

    def test_gold_bag_art_does_not_hide_left_vase(self):
        frame = self.frame("sea_48.jpg")
        items = self.vision.collectibles(frame)
        self.assertTrue(any(abs(x - 48) < 25 and abs(y - 731) < 30 for x, y, _ in items))
        filtered=self.vision.collectibles(frame,player_x=680)
        self.assertEqual(len(filtered),1)
        self.assertAlmostEqual(filtered[0][0],48,delta=25)
        # Already too late to cross the entire screen before it reaches the
        # diver. The old planner chased it all the way to the edge anyway.
        self.control.last_target=(680,1072)
        self.control.step(frame, (), dry_run=True, items=items,player_x=681)
        self.assertEqual(self.control.last_plan.mode,"HOLD")
        self.assertGreater(self.control.last_plan.goal,540)

    def test_virus_blocks_crossing_to_loot(self):
        frame = self.frame("sea_48_5.jpg")
        hazards = self.vision.hazards(frame)
        self.assertTrue(any(abs(x - 334) < 35 and y < 800 for x, y, _ in hazards))
        self.control.stop()
        self.control.last_target=(680,1072)
        self.control.step(frame, hazards, dry_run=True,
                          items=self.vision.collectibles(frame),player_x=680)
        self.assertNotEqual(self.control.last_plan.mode,"EVADE")
        self.assertGreater(self.control.last_plan.goal,540)

    def test_gameplay_hud_survives_orange_object_merging_with_oxygen_box(self):
        frame = self.frame("sea_56.jpg")
        self.assertEqual(self.vision.detect(frame).state, "PLAYING")
        self.assertTrue(any(abs(x - 190) < 30 for x, _, _ in self.vision.collectibles(frame)))

    def test_player_detector_ignores_magenta_reward_text(self):
        frame=self.frame("sea_64.jpg")
        self.assertLess(self.vision.player_x(frame,near_x=400),65)

    def test_player_detector_can_reacquire_at_opposite_edge(self):
        frame=self.frame("sea_48_5.jpg")
        self.assertLess(self.vision.player_x(frame,near_x=680),65)

    def test_late_run_player_reacquires_from_wrong_prior(self):
        frame=self.frame("motion_2014/recording_0700.jpg")
        self.assertAlmostEqual(self.vision.player_x(frame,near_x=295),103,delta=20)

    def test_coin_is_visible_when_diver_at_left_edge(self):
        frame = self.frame("sea_64.jpg")
        items = self.vision.collectibles(frame)
        self.assertTrue(any(abs(x - 144) < 30 and y < 800 for x, y, _ in items))
        self.control.stop()
        self.control.last_target=(54,1072)
        self.control.step(frame, (), dry_run=True,items=items,player_x=54)
        self.assertEqual(self.control.last_plan.mode,"COLLECT")
        self.assertAlmostEqual(self.control.last_plan.goal,144,delta=20)

    def test_recorded_object_track_predicts_intercept_before_arrival(self):
        self.control.stop()
        plans=[]
        for i,t in enumerate((49.6,49.8,50.0)):
            frame=self.frame(str(Path("motion_2014")/f"recording_{int(t*10):04d}.jpg"))
            self.control.step(frame,(),dry_run=True,
                              items=self.vision.collectibles(frame),player_x=162,now=t)
            plans.append(self.control.last_plan)
        self.assertEqual(plans[0].mode,"COLLECT")
        self.assertEqual(plans[0].target_id,plans[1].target_id)
        self.assertGreater(self.control.tracker.tracks[plans[1].target_id].vy,250)
        self.assertGreater(plans[1].goal,72)
        self.assertLess(plans[1].goal,130)

    def test_headless_home_title_does_not_look_like_gameplay_counters(self):
        frame=self.frame("sea_home_headless.png")
        self.assertEqual(frame.shape[:2],(720,324))
        self.assertEqual(self.vision.detect(frame).state,"HOME")

    def test_gameplay_counters_still_detect_at_headless_stream_size(self):
        for name in ("sea_48.jpg","sea_48_5.jpg","sea_56.jpg","sea_64.jpg"):
            with self.subTest(frame=name):
                frame=cv2.resize(self.frame(name),(324,720))
                self.assertEqual(self.vision.detect(frame).state,"PLAYING")


if __name__ == "__main__":
    unittest.main()
