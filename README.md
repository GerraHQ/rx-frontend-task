# rx_frontend - an RTL evaluation task by Gerra

A complete, toolchain-verified SystemVerilog task for training and testing autonomous
coding agents on hardware design. The agent is handed a near-working multi-module system
with seeded integration defects and has to make it correct; an automated grader scores
the result on simulation, synthesis, and lint.

[Gerra](https://gerra.com) builds original datasets and evaluations for AI training and
systematic trading, on infrastructure we own. RTL evaluation is part of our
code-intelligence work: original hardware-design tasks, each verified end-to-end on an
open-source EDA toolchain before it ships.

This repository is a **public reference sample** - one full task, with its grader and
answer key included so you can audit how we build and how we score. In a live evaluation
the answer key under `donotaccess/` is root-owned and unreadable by the agent; here it is
open for inspection.

---

## The task

`rx_frontend` is a link ingress front-end. A registered input stage feeds a vendored PULP
`fifo_v3` library FIFO, which feeds a sticky status register, all behind one valid/ready
interface. The library blocks (`rtl/lib/`) are provided correct and are not editable.
`fifo_v3` is a plain `full` / `empty` / `usage` FIFO, so the watermark and the true count
do not come for free - the **integration** has to build them on top:

- the watermark and its early backpressure, derived from the store's fill versus the
  margin (with one-cycle hysteresis), sized so the registered in-flight beat is never
  dropped - not off the raw full flag;
- the true occupancy count (`fifo_v3`'s `usage_o` truncates to 0 at full);
- sticky overflow, a status register fed by events from different blocks, and a latch-free
  fill encode at the top level.

The shipped `rtl/rx_frontend.sv` has three seeded integration defects (two functional,
one a synthesis/lint latch). The agent finds and fixes them. The leaf modules are
provided; the agent writes the wiring, timing, and control logic. The integration is
original to this task, so there is no memorized solution to retrieve.

## How it is graded

The hidden grader scores the agent's top module on three layers.

| Layer | Weight | What it checks |
|-------|-------:|----------------|
| Functional | 0.50 | Six hidden scenarios. Each runs the agent's `rx_frontend` and a golden reference on identical stimulus and compares every output, cycle by cycle. Partial credit per scenario. |
| Synthesis | 0.30 | Yosys `synth_ice40`: synthesizes, infers no latch (`$dlatch == 0`), and lands in a gate band. 0.10 each. |
| Lint | 0.20 | Clean under `verilator -Wall`. |

**Hard cap:** if functional is 0, total reward is 0 - a design that does not work scores
nothing, whatever it synthesizes to.

Scoring is a cycle-by-cycle diff against the reference, so the prompt pins the observable
timing contract it depends on - the one-cycle input registration, the valid/ready
semantics on both sides, the `level_o` encoding. If the agent meets that timing contract,
it matches the reference; we do not penalize a different internal microarchitecture. The
stimulus is reference-paced and
protocol-compliant (it holds `s_valid` and `s_data` until the reference accepts a beat),
so both modules always see the same legal traffic and any divergence is the agent's.

`ice40` is the synthesis target because this design is control-logic-dominant and small;
it gives a fast, deterministic latch check and gate count. Tasks with wide datapaths or
hard macros use a target sized to the design.

## Reward ladder (reproduces in one command)

```
python3 script/check_calibration.py
```

| Submission | Functional | Synthesis | Lint | Reward |
|------------|:----------:|:---------:|:----:|:------:|
| Vulnerable baseline (all three defects) | 0/6 | 0.20 | 0.00 | **0.00** |
| Functional fix, latch left behind | 6/6 | 0.20 | 0.00 | **0.70** |
| Golden integration | 6/6 | 0.30 | 1.00 | **1.00** |
| Correct RTL + forged scoreboard output | 0/6 | - | - | **0.00** |

Every number is produced by the grader running Verilator and Yosys, not hand-computed.
The 0.00 → 0.70 → 1.00 spread shows the grader separates a broken design (its three bugs
fail functional, which hard-caps the reward to 0), a partial fix, and a correct one. The
last row is the anti-forgery guard catching a correct design that prints fake scoreboard
output to claim a pass.

## Run it locally

Requires `verilator` and `yosys` on `PATH` (OSS CAD Suite or Homebrew).

```
make test     # simulate the shipped RTL against the directed sanity bench
make synth     # Yosys synth_ice40 + latch check
make lint      # verilator -Wall
python3 script/check_calibration.py   # reproduce the full reward ladder
```

`make test` fails on the shipped RTL and passes once the integration is correct; `make
synth` reports the inferred latch on the shipped fill encode and is clean once it is
fixed.

## Repository layout

```
prompt.md                    the task as the agent sees it
filelist.f                   RTL source list
Makefile                     test / lint / synth - mirror the hidden grader's flows
rtl/rx_frontend.sv           EDIT TARGET - shipped with three seeded integration defects
rtl/lib/fifo_v3.sv           vendored PULP library FIFO (correct; not editable)
rtl/lib/status_csr.sv        provided submodule (correct; not editable)
dv/visible_tb.sv             directed sanity bench (fails on the shipped RTL)
synth/synth.ys               Yosys proc + synth_ice40
script/check_latch.py        $dlatch detector
script/check_calibration.py  grader self-test (reproduces the reward ladder)
donotaccess/                 answer key - root-owned and uid-walled in a live eval
  rx_frontend_golden.sv      golden integration (scores 1.00)
  rx_frontend_ref.sv         golden reference compiled into the diff bench
  hidden_tb.sv               scoring bench (six-scenario cycle-by-cycle diff)
  grade.py                   three-layer grader
  lib/                       pristine submodule copies the grader compiles against
  variant/                   calibration variants (latch-left 0.70, forgery 0.00)
  bug_catalog.md             the three seeded defects
  task_note.md               task-design notes
```

## How the grader resists gaming

The grader is built assuming a capable agent will try to game it. The guards below close
the cheat patterns we have found so far; others may exist.

- **Forged scoreboard output.** The grader parses `SCENARIO ... PASS` lines, so an agent
  could print them. It strips comments and strings, then rejects any non-whitelisted `$`
  system task (`$display`, `$error`, macro tricks) and any `import "DPI-C"` or
  `` `include `` - these zero the functional score. A forgery variant in `donotaccess/`
  pins this with a regression test.
- **Lint suppression.** A `lint_off` pragma would hide the latch warning. The golden lints
  clean with no pragmas, so any lint pragma fails the lint layer for this task.
- **Oracle tampering.** The grader compiles the agent's file against pristine, root-owned
  copies of the library blocks, the reference, and the bench - editing those files does
  not change the score.
- **Scenario injection.** Only the six expected scenario names count; an unexpected name
  zeroes functional.

Diffing against a golden moves the spec out of a hand-written `expect` (which an agent can
satisfy while missing intent) and into the reference's behavior. The remaining blind spot
is stimulus coverage - which is why the scenarios are chosen to exercise the integration
corners the defects live in, and the golden itself is checked separately for correctness.

## The answer key

`donotaccess/` holds the golden, the hidden bench, the grader, and the calibration
variants. In a live evaluation it is root-owned and the agent runs under a dropped uid, so
it is unreadable to the agent. It is included in full here so a buyer can audit grader and
golden quality. Because the answer is public in this repo, this task is a demonstration
sample, not a production eval - production tasks ship with the key walled.

## About Gerra

Gerra originates proprietary datasets and evaluations on infrastructure we own. Tasks like
this one are authored and verified by our engineering team.

- Web: [gerra.com](https://gerra.com)
- Contact: [hello@gerra.com](mailto:hello@gerra.com)

## License

© Gerra. Provided as a reference sample for evaluation. All rights reserved. See
[LICENSE](LICENSE).
