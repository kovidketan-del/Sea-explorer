"""Isolated PyAV decoder for the internal scrcpy 4.1 packet protocol.

The parent owns Android sockets and sends exact 12-byte scrcpy packet headers
followed by their payloads on stdin. A native FFmpeg fault can then kill only
this child; the parent observes EOF, releases gameplay touch, and stops safely.
"""

from __future__ import annotations

import argparse
import struct
import sys
import time

import av


PACKET_CONFIG=1 << 62
PACKET_KEY=1 << 61
PACKET_PTS_MASK=PACKET_KEY-1
MAX_PACKET=8*1024*1024


def read_exact(stream,count):
    data=bytearray(count)
    view=memoryview(data)
    offset=0
    while offset<count:
        got=stream.readinto(view[offset:])
        if not got:
            return None
        offset+=got
    return data


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--codec",choices=("h264","h265"),required=True)
    args=parser.parse_args()
    decoder=av.CodecContext.create("hevc" if args.codec=="h265" else "h264","r")
    decoder.thread_count=1
    source=sys.stdin.buffer
    sink=sys.stdout.buffer
    while True:
        header=read_exact(source,12)
        if header is None:
            return 0
        pts_flags,length=struct.unpack(">QI",header)
        if not 0<length<=MAX_PACKET:
            raise ValueError(f"Invalid packet length {length}")
        payload=read_exact(source,length)
        if payload is None:
            raise EOFError("Truncated H.264 packet")
        if pts_flags&PACKET_CONFIG:
            decoder.extradata=bytes(payload)
            continue
        packet=av.Packet(payload)
        packet.pts=pts_flags&PACKET_PTS_MASK
        packet.dts=packet.pts
        started=time.perf_counter()
        frames=decoder.decode(packet)
        decoded=time.perf_counter()
        for frame in frames:
            array=frame.to_ndarray(format="bgr24")
            converted=time.perf_counter()
            h,w=array.shape[:2]
            sink.write(struct.pack(">IIQff",w,h,packet.pts,
                                   (decoded-started)*1000,(converted-decoded)*1000))
            sink.write(array.tobytes())
            sink.flush()


if __name__=="__main__":
    raise SystemExit(main())
