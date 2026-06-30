Repair the SystemVerilog integration in `rtl/rx_frontend.sv`. It is a link **ingress
front-end** that composes two provided, correct library blocks (in `rtl/lib/`, do not edit
them) into one backpressured receive path:

- `fifo_v3` - a vendored PULP elastic FIFO. It has a plain `full_o` / `empty_o` /
  `usage_o` interface and does **not** provide a watermark, an overflow flag, or a true
  occupancy count (`usage_o` wraps to 0 when the store is full).
- `status_csr` - a generic sticky status register.

Your integration is the deliverable: a **registered input beat** (one cycle of in-flight
latency), the **watermark** and its early backpressure, **sticky overflow** detection, the
**true occupancy count**, cross-block **status** aggregation, and a latch-free fill encode.
The library blocks are correct; the control and timing glue is not, and the current
version does not meet the contract below. Run `make test` to see where it fails, then fix
it. Do not change the module name or the port list, and do not add `$display` or other
simulation system tasks to the RTL; the testbench owns all status output.

The hidden grader checks this contract:

- **Data path.** Standard valid/ready on both sides. Accept an upstream beat only when
  `s_ready_o` is high; present a downstream beat only when `m_valid_o` is high; preserve
  FIFO order. `count_o` is the **true** occupancy - recover it, since `fifo_v3.usage_o`
  truncates to 0 at full. `m_data_o` is combinational from the store.
- **Early backpressure.** Derive `almost_full` from the store's fill versus
  `afull_margin_i` (free entries `<=` margin), with a one-cycle deassert hysteresis, and
  deassert `s_ready_o` at that **watermark** - not only when the store is full. The margin
  is sized so the registered in-flight beat always has a home, so a burst that hits the
  watermark must never drop or duplicate a beat. (Backpressuring only at *full* is a
  defect: it defeats the early-warning purpose of the watermark.)
- **Status (`status_o`, `err_o`), sticky, host-clearable via `sts_clear_i`.**
  - `status_o[1]` = **watermark hit**: set once `almost_full` has asserted at least once,
    held until clear/reset.
  - `status_o[0]` = **overflow**: set when a held beat is offered to a full store (the
    store drops it), held until clear/reset. With a watermark margin of 1 or more, correct
    backpressure keeps it from firing; at margin 0 it can.
  - `err_o` reflects the overflow fault.
- **`level_o`** is a coarse fill encode, free of inferred latches:
  `2'd0` empty, `2'd1` occupied (below the watermark), `2'd2` backpressuring, `2'd3` full.
  The `2'd2` "backpressuring" level tracks this module's `almost_full_o` output (the
  watermark, including its one-cycle deassert hysteresis).
- The design must pass Verilator simulation, synthesize **latch-free** under Yosys
  (`synth_ice40`) within the gate budget, and lint clean under `verilator -Wall` (your
  file - the vendored library blocks are read-only IP and are not linted against you).

`make test`, `make synth`, and `make lint` run flows equivalent to the hidden grader.
The hidden grader diffs your `rx_frontend` cycle-for-cycle against a golden reference
across backpressure, watermark + host-clear telemetry, fill/drain ordering, mid-stream
reset, simultaneous in/out, and overflow-pressure scenarios.

## What this task evaluates

The competency under test is composing an existing open-source FIFO (PULP `fifo_v3`) into a
correct backpressured path (HUD Design / repair track): deriving the watermark and the true
occupancy from the library's raw `full` / `empty` / `usage` outputs, early backpressure,
the one-cycle in-flight latency a registered input stage adds, sticky overflow, cross-block
status aggregation, and latch-free glue. It mirrors real RTL bring-up, where the work is
composing correct IP to a non-trivial spec, not authoring leaf modules. Scoring is the
three hidden layers above - functional 0.50 (cycle-accurate diff), synthesis 0.30, lint
0.20 - and functional 0 hard-caps the reward to 0.
