import threading
import time
import unittest

import numpy as np

from sea_explorer_bot import Sweeper, load_config


class FakeTouch:
    def __init__(self):
        self.actions=[]
        self.active=False
        self.lock=threading.Lock()

    def begin(self,x,y,w,h):
        with self.lock:
            self.actions.append(("BEGIN",x,y))
            self.active=True

    def move_to(self,x,y,duration_ms,w,h):
        with self.lock:
            self.actions.append(("MOVE",x,y,duration_ms))
        time.sleep(0.001)

    def interrupt(self):
        with self.lock:
            self.actions.append(("INTERRUPT",))

    def release(self):
        with self.lock:
            self.actions.append(("RELEASE",))
            self.active=False


class SweeperTests(unittest.TestCase):
    def setUp(self):
        self.touch=FakeTouch()
        self.cfg=load_config()
        self.cfg["motion"]["sweep_ms"]=5
        self.sweeper=Sweeper(self.touch,self.cfg)
        self.frame=np.zeros((2400,1080,3),np.uint8)

    def tearDown(self):
        if self.sweeper.active:
            self.sweeper.stop()

    def _wait_for_moves(self,count,timeout=.5):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            with self.touch.lock:
                moves=[x for x in self.touch.actions if x[0]=="MOVE"]
            if len(moves)>=count:
                return moves
            time.sleep(.005)
        return moves

    def test_continues_left_right_without_waiting_for_more_vision_frames(self):
        self.sweeper.step(self.frame,())
        moves=self._wait_for_moves(6)
        self.assertGreaterEqual(len(moves),6)
        xs=[m[1] for m in moves[:6]]
        self.assertEqual(xs,[1026,54,1026,54,1026,54])

    def test_hazard_update_parks_worker_then_clear_frames_resume(self):
        self.sweeper.step(self.frame,())
        self.assertGreaterEqual(len(self._wait_for_moves(2)),2)
        bomb=((540,1608,250),)
        self.sweeper.step(self.frame,bomb)
        time.sleep(.04)
        with self.touch.lock:
            before=len([x for x in self.touch.actions if x[0]=="MOVE"])
        self.sweeper.step(self.frame,())
        self.sweeper.step(self.frame,())
        moves=self._wait_for_moves(before+2)
        self.assertGreaterEqual(len(moves),before+2)

    def test_dry_run_never_starts_worker_or_injects_touch(self):
        reason,_=self.sweeper.step(self.frame,(),dry_run=True)
        self.assertIn("DRY-CONTINUOUS",reason)
        time.sleep(.02)
        self.assertEqual(self.touch.actions,[])
        self.assertFalse(self.sweeper.active)


if __name__=="__main__":
    unittest.main()
