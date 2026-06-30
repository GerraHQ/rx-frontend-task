#!/usr/bin/env python3
"""Report inferred latches from a yosys `select -count t:$dlatch` report
(`build/latch.rpt`, written by `make synth`). Exit 1 if any are present. Mirrors the
grader's latch-free synthesis check (latches counted at the post-proc stage)."""

import re
import sys
from pathlib import Path


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "build/latch.rpt")
    if not path.is_file():
        print(f"missing {path}; run `make synth` first")
        return 1
    match = re.search(r"(\d+)\s+objects", path.read_text(encoding="utf-8"))
    latches = int(match.group(1)) if match is not None else 0
    print(f"inferred latches ($dlatch): {latches}")
    if latches:
        print("FAIL: design infers a latch — a combinational output is not assigned on "
              "every path (complete the case / add a default).")
        return 1
    print("OK: latch-free")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
