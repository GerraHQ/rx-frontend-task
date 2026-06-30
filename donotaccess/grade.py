#!/usr/bin/env python3
"""Hidden three-layer grader for the rx_frontend integration task.

Layers (weights): functional 0.50 (hidden Verilator scenarios — the agent's rx_frontend
diffed cycle-accurately against the golden rx_frontend_ref), synthesis 0.30 (yosys
synth_ice40: success + latch-free + gate-band, 0.10 each), lint 0.20 (Verilator -Wall
clean). Hard cap: functional == 0 forces reward to 0.

Only the top integration file (rtl/rx_frontend.sv) is graded; the library blocks, the
golden reference, and the hidden testbench are taken from this root-owned directory, so
the agent cannot alter them.

Hardened past the HUD template baseline (changes flagged by an adversarial multi-model
review):
  - anti-forgery guard: agent RTL may use only a whitelist of synthesizable system
    functions ($clog2, ...); any other `$` (incl. $display/$error/$system or macro
    tricks) zeroes functional. Comments and string literals are stripped first so a
    legitimate comment mentioning $display does not false-trip.
  - lint-suppression guard: `lint_off`/`lint_on` pragmas in the RTL fail lint.
  - scenario-name validation: only the expected scenario names count.
"""

import argparse
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCENARIO_RE = re.compile(r"^SCENARIO\s+(?P<name>\S+)\s+(?P<status>PASS|FAIL)")
GATE_BAND = (150, 700)
EXPECTED_SCENARIOS = (
    "lossless_backpressure",
    "watermark_telemetry",
    "fill_drain_order",
    "reset_midstream",
    "simul_in_out",
    "overflow_pressure",
)
LIB_FILES = ("fifo_v3.sv", "status_csr.sv")
# Synthesizable system functions an agent may legitimately use; every other `$` token
# is treated as an injection / illegal-construct attempt.
ALLOWED_SYS = (
    "$clog2", "$signed", "$unsigned", "$bits", "$size", "$high", "$low",
    "$left", "$right", "$dimensions", "$onehot", "$onehot0", "$countones",
    "$isunknown", "$typename",
)

_drop = (
    ["setpriv", "--reuid", "1000", "--regid", "1000", "--clear-groups", "--"]
    if (hasattr(os, "geteuid") and os.geteuid() == 0)
    else []
)


def _chown_tree_to_agent(path: Path) -> None:
    if not (hasattr(os, "geteuid") and os.geteuid() == 0):
        return
    for item in [path, *path.rglob("*")]:
        try:
            os.chown(item, 1000, 1000)
        except (PermissionError, FileNotFoundError, NotADirectoryError):
            pass


def tool_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["LANG"] = "en_US.UTF-8"
    env["LC_CTYPE"] = "en_US.UTF-8"
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        env["HOME"] = "/home/agent"
    # Derive the OSS CAD Suite path from the (possibly overridden) HOME, not Path.home(),
    # so root-mode runs look under the agent home, not /root.
    home = Path(env.get("HOME", str(Path.home())))
    if platform.system() == "Darwin":
        path_parts = []
        homebrew_bin = Path("/opt/homebrew/bin")
        oss_bin = home / "utils" / "oss-cad-suite" / "bin"
        if homebrew_bin.is_dir():
            path_parts.append(str(homebrew_bin))
        if oss_bin.is_dir():
            path_parts.append(str(oss_bin))
        path_parts.extend(["/usr/bin", "/bin", "/usr/sbin", "/sbin"])
        path_parts.append(env.get("PATH", ""))
        env["PATH"] = ":".join(part for part in path_parts if part)
        env["AR"] = "/usr/bin/ar"
        env["RANLIB"] = "/usr/bin/ranlib"
    else:
        oss_bin = home / "utils" / "oss-cad-suite" / "bin"
        if oss_bin.is_dir():
            env["PATH"] = f"{oss_bin}:{env.get('PATH', '')}"
    return env


def run(args: list[str], *, cwd: Path, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    proc = subprocess.Popen(
        args, cwd=cwd, env=tool_env(), text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True,
    )
    try:
        stdout, _ = proc.communicate(timeout=timeout)
        return subprocess.CompletedProcess(args, proc.returncode, stdout, None)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            stdout, _ = proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            stdout = ""
        return subprocess.CompletedProcess(
            args, proc.returncode if proc.returncode is not None else -signal.SIGKILL,
            (stdout or "") + f"\n[grader] timed out after {timeout}s; process group killed\n", None,
        )


def count_cells(json_path: Path, cell_type: str | None = None) -> int:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    count = 0
    for module in data.get("modules", {}).values():
        for cell in module.get("cells", {}).values():
            if cell_type is None or cell.get("type") == cell_type:
                count += 1
    return count


def _parse_latch_count(path: Path) -> int | None:
    """Parse the count emitted by yosys `select -count t:$dlatch` (e.g. '1 objects.')."""
    match = re.search(r"(\d+)\s+objects", path.read_text(encoding="utf-8"))
    return int(match.group(1)) if match is not None else None


def _strip_comments_strings(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)   # block comments
    text = re.sub(r"//[^\n]*", " ", text)                # line comments
    text = re.sub(r'"(\\.|[^"\\])*"', " ", text)         # string literals
    return text


def injection_guard(rtl: Path) -> str | None:
    """Return a rejection reason if the agent RTL uses any non-whitelisted `$` system
    construct (stdout-forgery / illegal-construct guard); otherwise None. Lint
    suppression (`lint_off`) is handled separately in lint_score."""
    raw = rtl.read_text(encoding="utf-8", errors="ignore")
    # DPI and preprocessor-include escape hatches bypass the `$` scan entirely (DPI can
    # print/exec arbitrary C; an include can pull in forged content). A synthesizable
    # single-file top never needs either. Strip comments first (a commented-out form is
    # inert) but keep strings so the "DPI-C" literal stays visible.
    no_comments = re.sub(r"/\*.*?\*/", " ", raw, flags=re.S)
    no_comments = re.sub(r"//[^\n]*", " ", no_comments)
    if re.search(r'\b(?:import|export)\s+"DPI', no_comments):
        return "DPI import/export is not allowed"
    if re.search(r"`include\b", no_comments):
        return "`include is not allowed"
    scan = _strip_comments_strings(raw)
    for fn in ALLOWED_SYS:
        scan = scan.replace(fn, " ")
    if "$" in scan:
        leftover = sorted(set(re.findall(r"\$\s*\w*", scan)))
        return f"unauthorized system construct(s) {leftover or ['$']} (only {list(ALLOWED_SYS)} allowed)"
    return None


def _stage_sources(work: Path, rtl: Path, hidden_root: Path, extra: tuple[str, ...]) -> None:
    """Copy the agent top plus the pristine library/reference/TB sources into work."""
    shutil.copy2(rtl, work / "rx_frontend.sv")
    for name in LIB_FILES:
        shutil.copy2(hidden_root / "lib" / name, work / name)
    for name in extra:
        shutil.copy2(hidden_root / name, work / name)


def functional_score(rtl: Path, hidden_root: Path) -> dict[str, object]:
    total = len(EXPECTED_SCENARIOS)
    if not rtl.exists():
        return {"score": 0.0, "passed": 0, "total": total,
                "detail": "agent RTL file missing", "log": ""}
    reason = injection_guard(rtl)
    if reason is not None:
        return {"score": 0.0, "passed": 0, "total": total,
                "detail": f"injection guard tripped: {reason}", "log": ""}
    with tempfile.TemporaryDirectory(prefix="rx-frontend-func-") as td:
        work = Path(td)
        _stage_sources(work, rtl, hidden_root, ("rx_frontend_ref.sv", "hidden_tb.sv"))
        compile_result = run(
            ["verilator", "--binary", "--timing", "-Wno-fatal",
             "--top-module", "rx_frontend_hidden_tb",
             "-Mdir", "obj_hidden", "-o", "sim_hidden",
             "rx_frontend.sv", "fifo_v3.sv", "status_csr.sv",
             "rx_frontend_ref.sv", "hidden_tb.sv"],
            cwd=work, timeout=120,
        )
        if compile_result.returncode != 0:
            return {"score": 0.0, "passed": 0, "total": total,
                    "detail": "Verilator hidden simulation compile failed",
                    "log": compile_result.stdout}

        # remove the answer key (reference + scoreboard) before running the agent's logic
        (work / "hidden_tb.sv").unlink(missing_ok=True)
        (work / "rx_frontend_ref.sv").unlink(missing_ok=True)
        _chown_tree_to_agent(work)
        sim = work / "obj_hidden" / "sim_hidden"
        sim_result = run([*_drop, str(sim)], cwd=work, timeout=45)
        scenario_status: dict[str, str] = {}
        for line in sim_result.stdout.splitlines():
            match = SCENARIO_RE.match(line.strip())
            if match:
                scenario_status[match.group("name")] = match.group("status")

        seen = set(scenario_status)
        unexpected = seen - set(EXPECTED_SCENARIOS)
        missing = set(EXPECTED_SCENARIOS) - seen
        passed = sum(
            1 for name in EXPECTED_SCENARIOS if scenario_status.get(name) == "PASS"
        )
        if unexpected:
            # Extra scenario names can only come from tampering with a fixed TB.
            return {"score": 0.0, "passed": 0, "total": total,
                    "detail": f"unexpected scenario name(s) {sorted(unexpected)}",
                    "log": sim_result.stdout}
        if sim_result.returncode != 0:
            detail = "hidden simulation exited nonzero"
        elif missing:
            detail = f"missing scenario(s) {sorted(missing)}"
        else:
            detail = "hidden simulation completed"

        return {"score": passed / total, "passed": passed, "total": total,
                "scenarios": scenario_status, "detail": detail, "log": sim_result.stdout}


def synthesis_score(rtl: Path, hidden_root: Path) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="rx-frontend-synth-") as td:
        work = Path(td)
        _stage_sources(work, rtl, hidden_root, ())
        # Spec synthesis method: synthesize with -noflatten, detect inferred latches via
        # `select t:$dlatch` (run at the post-proc stage, before synth_ice40 techmaps
        # $dlatch away), and check the gate count against a tolerance band.
        script = (
            "read_verilog -sv rx_frontend.sv fifo_v3.sv status_csr.sv; "
            "hierarchy -top rx_frontend; "
            "proc; "
            "tee -q -o latch.rpt select -count t:$dlatch; "
            "synth_ice40 -noflatten -top rx_frontend -json synth_ice40.json"
        )
        result = run(["yosys", "-q", "-p", script], cwd=work, timeout=90)
        success = result.returncode == 0 and (work / "latch.rpt").is_file()
        latch_count = None
        cell_count = None
        if success:
            latch_count = _parse_latch_count(work / "latch.rpt")
            if (work / "synth_ice40.json").is_file():
                cell_count = count_cells(work / "synth_ice40.json")
        latch_free = bool(success and latch_count == 0)
        gate_in_band = bool(success and cell_count is not None and GATE_BAND[0] <= cell_count <= GATE_BAND[1])
        weighted = (0.10 if success else 0.0) + (0.10 if latch_free else 0.0) + (0.10 if gate_in_band else 0.0)
        return {"score": weighted, "synthesis_success": success, "latch_count": latch_count,
                "cell_count": cell_count, "gate_band": list(GATE_BAND), "gate_in_band": gate_in_band,
                "detail": "synthesis completed" if success else "synthesis failed", "log": result.stdout}


def lint_score(rtl: Path, hidden_root: Path) -> dict[str, object]:
    raw = rtl.read_text(encoding="utf-8", errors="ignore")
    if re.search(r"\blint_o(ff|n)\b", raw):
        return {"score": 0.0, "warning_count": 0, "warnings": [],
                "detail": "lint-suppression pragma present", "log": ""}
    with tempfile.TemporaryDirectory(prefix="rx-frontend-lint-") as td:
        work = Path(td)
        _stage_sources(work, rtl, hidden_root, ())
        result = run(
            ["verilator", "--lint-only", "-Wall", "-Wno-fatal",
             "--top-module", "rx_frontend",
             "rx_frontend.sv", "fifo_v3.sv", "status_csr.sv"],
            cwd=work, timeout=45,
        )
        # Lint only the agent's file; the vendored library blocks are read-only IP and may
        # carry their own benign warnings (e.g. an unused fifo_v3 test-mode port).
        warning_lines = [ln for ln in result.stdout.splitlines()
                         if ln.startswith("%Warning-") and "rx_frontend.sv:" in ln]
        error_lines = [ln for ln in result.stdout.splitlines() if ln.startswith("%Error")]
        score = 1.0 if (not warning_lines and not error_lines) else 0.0
        return {"score": score, "warning_count": len(warning_lines), "warnings": warning_lines,
                "detail": "lint clean" if score == 1.0 else "lint warnings present", "log": result.stdout}


def grade(root: Path, rtl_override: Path | None, hidden_root: Path | None = None) -> dict[str, object]:
    rtl = rtl_override if rtl_override is not None else root / "rtl" / "rx_frontend.sv"
    if hidden_root is None:
        local_hidden_root = root / "donotaccess"
        hidden_root = local_hidden_root if local_hidden_root.is_dir() else Path(__file__).parent

    functional = functional_score(rtl, hidden_root)
    synthesis = synthesis_score(rtl, hidden_root)
    lint = lint_score(rtl, hidden_root)

    functional_weighted = 0.50 * float(functional["score"])
    synthesis_weighted = float(synthesis["score"])
    lint_weighted = 0.20 * float(lint["score"])
    reward = round(functional_weighted + synthesis_weighted + lint_weighted, 6)
    hard_caps: list[str] = []
    if float(functional["score"]) == 0.0:
        reward = 0.0
        hard_caps.append("functional_score_zero")

    return {
        "reward": reward,
        "hard_caps": hard_caps,
        "subscores": {
            "functional": {"weight": 0.50, "raw_score": functional["score"],
                           "weighted_score": functional_weighted, "result": functional},
            "synthesis": {"weight": 0.30, "weighted_score": synthesis_weighted, "result": synthesis},
            "lint": {"weight": 0.20, "raw_score": lint["score"],
                     "weighted_score": lint_weighted, "result": lint},
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--rtl", default=None)
    parser.add_argument("--hidden-root", default=None)
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument("--fail-below", type=float, default=None)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    rtl_override = Path(args.rtl).resolve() if args.rtl else None
    hidden_root = Path(args.hidden_root).resolve() if args.hidden_root else None
    result = grade(root, rtl_override, hidden_root)
    print(json.dumps(result, indent=2 if args.pretty else None, sort_keys=True))
    if args.fail_below is not None and float(result["reward"]) < args.fail_below:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
