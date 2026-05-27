"""Quick diagnostic: prints the pixel values the BlackCrossDetector inspects.

Run:  python debug_vistek.py <file>  [--frames N]
"""
import sys
import argparse
from fractions import Fraction
import av
import numpy as np

p = argparse.ArgumentParser()
p.add_argument("input")
p.add_argument("--frames", type=int, default=200, help="How many video frames to sample")
p.add_argument("--sample-x", type=int, default=10)
args = p.parse_args()

with av.open(args.input) as container:
    vstream = container.streams.video[0]
    print(f"codec:       {vstream.codec_context.name}")
    print(f"pix_fmt:     {vstream.codec_context.pix_fmt}")
    print(f"resolution:  {vstream.codec_context.width}x{vstream.codec_context.height}")
    print()

    n = 0
    for packet in container.demux(vstream):
        for frame in packet.decode():
            arr = frame.to_ndarray(format="rgb24")
            h, w = arr.shape[:2]
            y_mid = h // 2

            lp = arr[0, 0]           # colorbar validity: first line, far left
            rp = arr[0, w - 1]       # colorbar validity: first line, far right
            cp = arr[y_mid, min(args.sample_x, w - 1)]  # cross detection pixel

            thr_w = 242  # 95% of 255
            white_ok = all(int(v) >= 200 for v in lp)
            black_ok = int(rp[0]) < 39 and int(rp[1]) < 40 and int(rp[2]) < 40
            cross_detected = not all(int(v) >= thr_w for v in cp)

            pts_s = float(frame.pts * frame.time_base) if frame.pts is not None else 0.0
            flags = []
            if not white_ok: flags.append("NO_WHITE_LEFT")
            if not black_ok: flags.append("NO_BLACK_RIGHT")
            if cross_detected: flags.append("CROSS_DETECTED")

            print(f"t={pts_s:7.3f}s  "
                  f"row0[0]={tuple(lp)}  row0[{w-1}]={tuple(rp)}  "
                  f"mid[{args.sample_x}]={tuple(cp)}  "
                  + ("  ".join(flags) if flags else "ok"))

            n += 1
            if n >= args.frames:
                sys.exit(0)
