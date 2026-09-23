import struct
import unittest

import cv2
import numpy as np

from sea_explorer_bot import ADB


class RawScreencapTests(unittest.TestCase):
    def test_decodes_12_byte_rgba_frame(self):
        rgba=np.array([
            [[255,0,0,255],[0,255,0,255]],
            [[0,0,255,255],[255,255,255,255]],
        ],dtype=np.uint8)
        raw=struct.pack("<III",2,2,1)+rgba.tobytes()
        frame=ADB._decode_raw_screencap(raw)
        self.assertEqual(frame.shape,(2,2,3))
        self.assertEqual(frame[0,0].tolist(),[0,0,255])
        self.assertEqual(frame[1,0].tolist(),[255,0,0])

    def test_decodes_16_byte_rgba_frame(self):
        rgba=np.zeros((2,3,4),dtype=np.uint8)
        rgba[:,:,:3]=[10,20,30]
        rgba[:,:,3]=255
        raw=struct.pack("<IIII",3,2,1,0)+rgba.tobytes()
        frame=ADB._decode_raw_screencap(raw)
        self.assertEqual(frame.shape,(2,3,3))
        self.assertEqual(frame[0,0].tolist(),[30,20,10])

    def test_invalid_raw_returns_none(self):
        self.assertIsNone(ADB._decode_raw_screencap(b"bad"))


if __name__=="__main__":
    unittest.main()
