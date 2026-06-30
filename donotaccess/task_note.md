# Task Notes - rx_frontend (Design / repair, multi-module system)

The five task-design answers HUD requests, for this sample.

**1. Core RTL competency evaluated.** Multi-module integration and cross-module flow
control. The agent must reason about composing valid/ready interfaces, early-watermark
backpressure versus a raw full flag, the one-cycle in-flight latency a registered input
stage adds, aggregating sticky status from events sourced in different blocks, and
latch-free combinational glue. The library blocks are given correct - the agent cannot
score by retrieving or rewriting a textbook part; it must get the *integration* right.

**2. Workflow realism.** Ingress front-ends that pair watermark-driven backpressure with
a sticky status/error register are standard (NIC RX paths, NoC ingress, AXI/stream
adapters, link-level flow control). The bulk of real RTL effort is integration and
bring-up of provided IP, not authoring leaf modules - and "wire these correct blocks
together, the timing/handshake is the deliverable" is exactly that work. The
near-working start point with integration-level defects mirrors a bring-up/repair
ticket, not a greenfield toy.

**3. Verification / rubric breakdown.** Three hidden layers. Functional 0.50: six
scenarios (backpressure, watermark + host-clear telemetry, fill/drain order, mid-stream
reset, simultaneous in/out, overflow pressure), each a **cycle-accurate diff** of the
agent's `rx_frontend` against a golden `rx_frontend_ref` under reference-paced,
protocol-compliant stimulus (`s_valid` stays high and `s_data` holds until the reference
accepts the beat, so both modules see identical inputs and any divergence is the agent's
defect), partial credit per scenario; the functional layer also rejects RTL that emits
simulation output or reaches outside the sandbox (`$display`, DPI, `` `include ``), so the
scoreboard stdout cannot be forged. Synthesis 0.30:
Yosys `synth_ice40`, 0.10 each for success, latch-free (`$dlatch`==0), and gate count in
a 150-700 band. Lint 0.20: Verilator `-Wall` clean (a `lint_off` pragma fails the
layer). Hard cap: functional == 0 forces reward to 0.

**4. Model exploration surface.** The agent should read `prompt.md` (the contract),
`rtl/rx_frontend.sv` (edit target), `rtl/lib/fifo_v3.sv` and `rtl/lib/status_csr.sv`
(the provided blocks - it must understand fifo_v3's `full` / `empty` / `usage` interface,
that `usage` truncates to 0 at full, and the `status_csr` event interface), `dv/visible_tb.sv` (a
directed sanity test that already fails on the shipped RTL), `Makefile`, `synth/synth.ys`,
and `script/check_latch.py`. Solution path: run `make test` to see the early-backpressure
and telemetry failures; change `s_ready_o` to backpressure off the watermark; source the
watermark-hit event from `almost_full`; complete the fill-encode case to clear the latch;
iterate against `make synth` and `make lint`.

**5. Failure modes the task is built to separate.** (a) *Backpressures on the full flag*: reuses
the FIFO's `wr_ready` for `s_ready_o`, missing the watermark's early-warning intent -
passes a naive data test, fails the backpressure diff. (b) *Latch blindness*: fixes the
functional wiring but never runs synthesis and ships the `level_o` latch, capping at
0.70. (c) *Over-eager backpressure*: gates `s_ready_o` on `almost_full` but also breaks
the in-flight load/drain handshake, so a beat stalls or duplicates - fails the
simultaneous in/out diff. (d) *Wrong telemetry source*: wires watermark-hit to the full
flag instead of `almost_full`, setting the bit late - fails the telemetry scenario.
