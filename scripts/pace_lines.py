#!/usr/bin/env python3
"""Stream input lines at an exact average line rate without a startup burst."""

import argparse
import sys
import time


parser = argparse.ArgumentParser()
parser.add_argument("--lines-per-second", type=float, required=True)
args = parser.parse_args()
if args.lines_per_second <= 0:
    raise SystemExit("--lines-per-second must be positive")

interval = 1.0 / args.lines_per_second
deadline = time.monotonic()
for line in sys.stdin:
    deadline += interval
    remaining = deadline - time.monotonic()
    if remaining > 0:
        time.sleep(remaining)
    sys.stdout.write(line)
    sys.stdout.flush()
