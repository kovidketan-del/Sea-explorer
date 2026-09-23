"""Offline safety checks for the window-free scrcpy transport."""

import struct
import time
import unittest

import numpy as np

from headless_scrcpy import HeadlessScrcpy, ScrcpySocketTouch, StreamUnavailable
from sea_explorer_bot import ADB


class RecordingSocket:
    def __init__(self):
        self.messages=[]

    def sendall(self, data):
        self.messages.append(data)


class Runtime:
    def __init__(self):
        self.control_socket=RecordingSocket()


class SocketTouchTests(unittest.TestCase):
    def test_down_move_up_is_one_pointer_in_video_coordinates(self):
        runtime=Runtime()
        touch=ScrcpySocketTouch(runtime)
        touch.begin(162,480,324,720)
        touch.move_to(250,480,0,324,720)
        touch.release()
        self.assertEqual(len(runtime.control_socket.messages),3)
        parsed=[struct.unpack(">BBQIIHHHII",raw) for raw in runtime.control_socket.messages]
        self.assertEqual([message[1] for message in parsed],[0,2,1])
        self.assertEqual([(message[3],message[4]) for message in parsed],[(162,480),(250,480),(250,480)])
        self.assertTrue(all((message[5],message[6])==(324,720) for message in parsed))
        self.assertTrue(all(message[2]==parsed[0][2] for message in parsed))
        self.assertFalse(touch.active)

    def test_resize_releases_before_raising(self):
        runtime=Runtime()
        touch=ScrcpySocketTouch(runtime)
        touch.begin(100,200,324,720)
        with self.assertRaisesRegex(StreamUnavailable,"dimensions changed"):
            touch.move_to(150,200,30,400,720)
        self.assertEqual(len(runtime.control_socket.messages),2)
        self.assertFalse(touch.active)


class LatestFrameTests(unittest.TestCase):
    def test_returns_latest_without_building_frame_queue(self):
        runtime=HeadlessScrcpy.__new__(HeadlessScrcpy)
        import threading
        runtime._condition=threading.Condition()
        frame=np.zeros((2,3,3),np.uint8)
        runtime._latest=(frame,time.perf_counter(),time.perf_counter(),0)
        runtime._sequence=5
        runtime._last_delivered=0
        runtime._error=None
        self.assertIs(runtime.capture(timeout=0),frame)
        self.assertEqual(runtime._last_delivered,5)
        self.assertLess(runtime.last_frame_age,.1)

    def test_stream_error_fails_closed(self):
        runtime=HeadlessScrcpy.__new__(HeadlessScrcpy)
        import threading
        runtime._condition=threading.Condition()
        runtime._latest=None
        runtime._sequence=0
        runtime._last_delivered=0
        runtime._error=OSError("socket gone")
        with self.assertRaisesRegex(StreamUnavailable,"socket gone"):
            runtime.capture(timeout=0)


class PhoneLockTests(unittest.TestCase):
    def test_keyguard_state_blocks_misclassified_gameplay(self):
        adb=ADB.__new__(ADB)
        adb.run=lambda *args,**kwargs: "KeyguardStateMonitor\n        mIsShowing=true\n        mInputRestricted=true"
        self.assertTrue(adb.is_keyguard_locked())
        adb.run=lambda *args,**kwargs: "KeyguardStateMonitor\n        mIsShowing=false"
        self.assertFalse(adb.is_keyguard_locked())


if __name__=="__main__":
    unittest.main()
