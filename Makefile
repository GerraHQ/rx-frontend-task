# rx_frontend task — local flows mirroring the hidden grader (open-source EDA only).
# Requires verilator and yosys on PATH (OSS CAD Suite, or Homebrew on macOS).
#
#   make test   — run the visible directed testbench (functional sanity)
#   make lint   — verilator -Wall on the design
#   make synth  — yosys synth_ice40 + inferred-latch report
#   make clean

RTL   = rtl/rx_frontend.sv rtl/lib/fifo_v3.sv rtl/lib/status_csr.sv
TOP   = rx_frontend
VTB   = dv/visible_tb.sv
BUILD = build

.PHONY: test lint synth clean

test:
	@mkdir -p $(BUILD)
	verilator --binary --timing -Wno-fatal --top-module visible_tb \
	  -Mdir $(BUILD)/obj_vis -o sim_vis $(RTL) $(VTB)
	@./$(BUILD)/obj_vis/sim_vis

lint:
	verilator --lint-only -Wall -Wno-fatal --top-module $(TOP) $(RTL)

synth:
	@mkdir -p $(BUILD)
	yosys -q -p "read_verilog -sv $(RTL); hierarchy -top $(TOP); proc; \
	  tee -q -o $(BUILD)/latch.rpt select -count t:\$$dlatch; \
	  synth_ice40 -noflatten -top $(TOP) -json $(BUILD)/synth_ice40.json; stat"
	@python3 script/check_latch.py $(BUILD)/latch.rpt

clean:
	rm -rf $(BUILD)
