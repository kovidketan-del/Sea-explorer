import unittest
from unittest.mock import patch

import numpy as np

from sea_explorer_bot import BotError, Sweeper, load_config


class FakeTouch:
    def __init__(self):
        self.actions=[]

    def begin(self,x,y,w,h):
        self.actions.append(("DOWN",x,y))

    def move_to(self,x,y,duration_ms,w,h):
        self.actions.append(("MOVE",x,y,duration_ms))

    def release(self):
        self.actions.append(("UP",))


class SweeperTests(unittest.TestCase):
    def setUp(self):
        self.touch=FakeTouch()
        self.sweeper=Sweeper(self.touch,load_config())
        self.frame=np.zeros((2400,1080,3),np.uint8)

    def test_one_observed_pass_at_fixed_height_then_reverse(self):
        first=self.sweeper.step(self.frame,())
        second=self.sweeper.step(self.frame,())
        self.assertEqual(first[1],(1026,1608))
        self.assertEqual(second[1],(54,1608))
        self.assertEqual(self.touch.actions[:3],[
            ("DOWN",540,1608),
            ("MOVE",1026,1608,250),
            ("MOVE",54,1608,250),
        ])
        self.assertEqual(self.sweeper.swipes_sent,2)
        self.sweeper.stop()
        self.assertEqual(self.touch.actions[-1],("UP",))

    def test_initial_bomb_holds_position_but_starts_continuous_touch(self):
        reason,end=self.sweeper.step(self.frame,((900,900,175),))
        self.assertIn("PARK-SAFE",reason)
        self.assertEqual(end,(540,1608))
        self.assertEqual(self.touch.actions,[("DOWN",540,1608)])
        self.assertTrue(self.sweeper.active)

    def test_parks_at_safe_edge_until_two_clear_frames(self):
        self.sweeper.step(self.frame,())
        bomb=((200,1300,175),)
        reason,end=self.sweeper.step(self.frame,bomb)
        self.assertIn("PARK-SAFE",reason)
        self.assertEqual(end,(1026,1608))
        self.assertEqual(len(self.touch.actions),2)
        self.assertIn("PARK-CONFIRM",self.sweeper.step(self.frame,())[0])
        self.assertEqual(len(self.touch.actions),2)
        self.assertIn("SWEEP",self.sweeper.step(self.frame,())[0])
        self.assertEqual(self.touch.actions[-1],("MOVE",54,1608,250))

    def test_bomb_in_row_never_causes_a_crossing_or_diagonal(self):
        self.sweeper.step(self.frame,())
        self.sweeper.step(self.frame,())
        bomb=((540,1608,175),)
        reason,end=self.sweeper.step(self.frame,bomb)
        self.assertIn("PARK-SAFE",reason)
        self.assertEqual(end,(54,1608))
        self.assertEqual(len(self.touch.actions),3)

    def test_early_bomb_moves_to_opposite_edge_before_it_arrives(self):
        self.sweeper.step(self.frame,())
        reason,end=self.sweeper.step(self.frame,((900,900,175),))
        self.assertIn("PARK-MOVE",reason)
        self.assertEqual(end,(54,1608))
        self.assertEqual(self.touch.actions[-1],("MOVE",54,1608,250))

    def test_late_bomb_escapes_down_and_locks_lane(self):
        self.sweeper.step(self.frame,())
        bomb=((900,1400,175),)
        reason,end=self.sweeper.step(self.frame,bomb)
        self.assertIn("ESCAPE",reason)
        self.assertEqual(end,(1026,1967))
        self.assertEqual(self.touch.actions[-1],("MOVE",1026,1967,180))
        reason,end=self.sweeper.step(self.frame,bomb)
        self.assertIn("PARK-ESCAPED",reason)
        self.assertEqual(end,(1026,1967))
        self.assertEqual(len(self.touch.actions),3)

    def test_dry_run_never_injects_touch(self):
        self.sweeper.step(self.frame,(),dry_run=True)
        self.sweeper.stop()
        self.assertEqual(self.touch.actions,[])

    def test_release_failure_stops_bot(self):
        self.sweeper.step(self.frame,())
        def fail_release():
            raise RuntimeError("button release failed")
        self.touch.release=fail_release
        with patch("sea_explorer_bot.log"), self.assertRaises(BotError):
            self.sweeper.stop()
        self.assertTrue(self.sweeper.release_failed)


if __name__=="__main__":
    unittest.main()
