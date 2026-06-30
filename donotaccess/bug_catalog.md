# Bug Catalog - rx_frontend (integration repair)

Three bugs are seeded in the agent-facing `rtl/rx_frontend.sv`. All three are
**integration** defects in the top-level glue around the two provided library blocks (the
vendored PULP `fifo_v3` and `status_csr`), which are correct and untouched. Two are
functional; one is a hardware-quality (latch) defect. The mix means the task does not
reduce to a single category or a single insight.

1. **Backpressure off the full flag, not the watermark** (functional, integration)
   - Buggy: `assign s_ready_o = in_can_load && !fifo_full;` - uses `fifo_v3`'s raw full
     flag for upstream backpressure instead of the watermark.
   - Effect: no early backpressure. `s_ready_o` stays high through the entire
     almost-full zone, so the store fills to full instead of stopping at the watermark.
     The `s_ready` / `count_o` / `almost_full_o` trajectories diverge from the reference,
     and a registered in-flight beat arriving at a full store can even raise overflow.
     Defeats the whole purpose of the watermark. Fails the backpressure scenarios.
   - Fix: `assign s_ready_o = in_can_load && !almost_full_o;`

2. **Watermark-hit telemetry not aggregated** (functional, status)
   - Buggy: `status_csr ... .event_i ({1'b0, overflow_event})` - the watermark event is
     tied to 0.
   - Effect: `status_o[1]` (watermark hit) never sets, even after the watermark asserts
     `almost_full`. Diverges from the reference in the telemetry scenario.
   - Fix: source the watermark event from the watermark: `.event_i ({almost_full_o, overflow_event})`.

3. **Inferred latch in the fill encode** (synthesis / lint)
   - Buggy: `always @*` case over `{fifo_full, fifo_empty}` with the `2'b11` arm and the
     default both omitted.
   - Effect: synthesis infers a `$dlatch`; Verilator `-Wall` reports LATCH. `{full,
     empty} == 2'b11` is unreachable, so `level_o` stays functionally correct - a patch
     that fixes (1) and (2) but leaves this scores ~0.70, the intended partial-credit
     checkpoint.
   - Fix: add a default arm (or `always_comb` with full coverage).

## Verified reward ladder (`script/check_calibration.py`)

| Submission                          | functional | synthesis | lint | reward |
|-------------------------------------|-----------:|----------:|-----:|-------:|
| vulnerable baseline (all 3 bugs)    | 0/6 = 0.00 |      0.20 | 0.00 |  0.00* |
| functional fixed, latch remaining   | 6/6 = 1.00 |      0.20 | 0.00 |  0.70  |
| golden                              | 6/6 = 1.00 |      0.30 | 1.00 |  1.00  |

\* Functional 0 hard-caps the reward to 0, so the baseline's synth 0.20 does not count.

Gate band: 150-700 ICE40 cells (golden synthesises to 296; the buggy/latch-left
variants to 293-297). Numbers produced by this grader running Verilator + Yosys, not
hand-computed.

## Grader hardening

Built against the cheat patterns a capable agent will try, each verified on the toolchain:

- **Cycle-accurate diff, not hand-written expects.** The functional layer diffs the
  agent's `rx_frontend` against the golden `rx_frontend_ref` over six scenarios with
  reference-paced, protocol-compliant stimulus (`s_valid` held high and `s_data` held
  stable until the reference accepts the beat - identical to both modules), so a subtly
  wrong design is far less likely to slip through, and the scoring oracle is the
  reference's own behavior rather than a hand-written expect.
- **Anti-forgery guard.** `functional_score` allows only a whitelist of synthesizable
  system functions (`$clog2`, ...); any other `$` construct (`$display`, `$error`,
  `$system`, or macro tricks) zeroes functional, so the agent cannot forge the
  `SCENARIO ... PASS` stdout. `import "DPI-C"` and `` `include `` - which bypass the `$`
  scan - are rejected outright. Comments and string literals are stripped first.
  `check_calibration.py` carries a `forged_pass` variant (correct RTL + an injected
  `$display` forgery) and asserts the grader returns reward 0 - a regression lock.
- **No lint suppression.** A `lint_off` / `lint_on` pragma fails the lint layer, so the
  latch cannot be hidden from `-Wall`.
- **Pristine, root-owned submodules.** The grader compiles the agent's top against
  root-owned copies of the library blocks, the reference, and the testbench - editing
  `rtl/lib/*` has no effect on the score.
- **Scenario-name validation.** Only the six expected scenario names count; an
  unexpected name (TB tampering) zeroes functional.
