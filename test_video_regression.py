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
        goal, mode, _ = self.control._plan(720, 1600, (), items, 680)
        self.assertEqual((goal, mode), (48, "COLLECT"))

    def test_virus_blocks_crossing_to_loot(self):
        frame = self.frame("sea_48_5.jpg")
        hazards = self.vision.hazards(frame)
        self.assertTrue(any(abs(x - 334) < 35 and y < 800 for x, y, _ in hazards))
        goal, mode, _ = self.control._plan(720, 1600, hazards, self.vision.collectibles(frame), 680)
        self.assertEqual((goal, mode), (680, "HOLD"))

    def test_gameplay_hud_survives_orange_object_merging_with_oxygen_box(self):
        frame = self.frame("sea_56.jpg")
        self.assertEqual(self.vision.detect(frame).state, "PLAYING")
        self.assertTrue(any(abs(x - 190) < 30 for x, _, _ in self.vision.collectibles(frame)))

    def test_coin_is_visible_when_diver_at_left_edge(self):
        frame = self.frame("sea_64.jpg")
        items = self.vision.collectibles(frame)
        self.assertTrue(any(abs(x - 144) < 30 and y < 800 for x, y, _ in items))
        goal, mode, _ = self.control._plan(720, 1600, (), items, 54)
        self.assertEqual((goal, mode), (144, "COLLECT"))


if __name__ == "__main__":
    unittest.main()
