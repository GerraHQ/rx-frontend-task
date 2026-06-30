#!/usr/bin/env python3
"""Grader calibration self-test: run the hidden grader against four known submissions
and assert the reward ladder. Run from the task root:

    python3 script/check_calibration.py
    # vulnerable 0.00 (hard cap), latch-left 0.70, golden 1.00, forgery 0.00
"""

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GRADE_PATH = ROOT / "donotaccess" / "grade.py"


def load_grade_module():
    spec = importlib.util.spec_from_file_location("rx_frontend_grade", GRADE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {GRADE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    grade_mod = load_grade_module()
    variant = ROOT / "donotaccess" / "variant"
    cases = [
        ("vulnerable_baseline", None, 0.0),  # all 3 bugs -> functional 0/6 -> hard cap
        ("golden_reference", ROOT / "donotaccess" / "rx_frontend_golden.sv", 1.0),
        ("functional_fixed_latch_remaining", variant / "rx_frontend_latch_left.sv", 0.70),
        ("forged_pass_injection", variant / "rx_frontend_forged_pass.sv", 0.0),
    ]
    ok = True
    for name, rtl, expected in cases:
        result = grade_mod.grade(ROOT, rtl, ROOT / "donotaccess")
        reward = float(result["reward"])
        passed = result["subscores"]["functional"]["result"]["passed"]
        total = result["subscores"]["functional"]["result"]["total"]
        print(f"{name}: reward={reward:.3f} expected={expected:.3f}  (functional {passed}/{total})")
        if abs(reward - expected) > 1e-6:
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
