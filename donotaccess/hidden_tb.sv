// hidden_tb — scoring testbench for the rx_frontend task (root-owned; never shipped).
//
// Drives identical stimulus into the agent's `rx_frontend` (DUT) and the golden
// `rx_frontend_ref`, and compares every observable output each cycle. A scenario PASSES
// only if no output ever diverges from the reference. The producer is a protocol-
// compliant valid/ready driver paced by the reference: s_valid is held high and s_data
// is held stable until the reference accepts the beat, so the stimulus is identical to
// both modules and idiomatic, while any DUT divergence (a wrong s_ready, a missing
// status bit, a wrong count) still shows up as a mismatch.
//
// Coverage: backpressure, watermark + host-clear telemetry, fill/drain ordering,
// mid-stream reset, simultaneous in/out, and a margin-0 overflow-pressure case.
// Emits one `SCENARIO <name> PASS|FAIL` line per scenario and a `SUMMARY hidden N/T`.
`timescale 1ns/1ps
module rx_frontend_hidden_tb;
    localparam int W  = 8;
    localparam int D  = 8;
    localparam int CW = $clog2(D + 1);

    logic clk = 1'b0, reset = 1'b1;
    always #5 clk = ~clk;

    // shared stimulus
    logic [W-1:0]  s_data;  logic s_valid;
    logic          m_ready;
    logic [CW-1:0] afull_margin;
    logic          sts_clear;

    // DUT outputs
    logic s_ready_d, m_valid_d, almost_full_d, err_d;
    logic [W-1:0] m_data_d; logic [1:0] status_d, level_d; logic [CW-1:0] count_d;
    // reference outputs
    logic s_ready_r, m_valid_r, almost_full_r, err_r;
    logic [W-1:0] m_data_r; logic [1:0] status_r, level_r; logic [CW-1:0] count_r;

    rx_frontend #(.width_p(W), .depth_p(D)) dut (
        .clk_i(clk), .reset_i(reset),
        .s_data_i(s_data), .s_valid_i(s_valid), .s_ready_o(s_ready_d),
        .m_data_o(m_data_d), .m_valid_o(m_valid_d), .m_ready_i(m_ready),
        .afull_margin_i(afull_margin), .sts_clear_i(sts_clear),
        .almost_full_o(almost_full_d), .status_o(status_d), .err_o(err_d),
        .level_o(level_d), .count_o(count_d)
    );
    rx_frontend_ref #(.width_p(W), .depth_p(D)) refm (
        .clk_i(clk), .reset_i(reset),
        .s_data_i(s_data), .s_valid_i(s_valid), .s_ready_o(s_ready_r),
        .m_data_o(m_data_r), .m_valid_o(m_valid_r), .m_ready_i(m_ready),
        .afull_margin_i(afull_margin), .sts_clear_i(sts_clear),
        .almost_full_o(almost_full_r), .status_o(status_r), .err_o(err_r),
        .level_o(level_r), .count_o(count_r)
    );

    int scn_fail;

    function automatic bit mismatch();
        mismatch = (s_ready_d    !== s_ready_r)
                || (m_valid_d     !== m_valid_r)
                || (m_valid_r && (m_data_d !== m_data_r))   // m_data is don't-care when !m_valid
                || (almost_full_d !== almost_full_r)
                || (status_d      !== status_r)
                || (err_d         !== err_r)
                || (level_d       !== level_r)
                || (count_d       !== count_r);
    endfunction

    // One scenario: synchronous reset, then `len` cycles of stimulus. The producer
    // holds s_valid high and advances s_data only when the reference accepts a beat
    // (protocol-compliant valid/ready). cons_k: m_ready asserted 1-in-cons_k.
    // reset_cyc / clear_cyc: pulse reset / sts_clear mid-run (-1 = none).
    task automatic run_scn(input string name, input int margin, input int cons_k,
                           input int len, input int reset_cyc, input int clear_cyc);
        int di;
        reset = 1'b1; s_valid = 1'b0; s_data = '0; m_ready = 1'b0;
        afull_margin = margin[CW-1:0]; sts_clear = 1'b0;
        repeat (2) @(posedge clk);
        @(negedge clk); reset = 1'b0;
        scn_fail = 0; di = 0;
        for (int c = 0; c < len; c++) begin
            s_valid   = 1'b1;
            s_data    = di[W-1:0];
            m_ready   = (c % cons_k == 0);
            reset     = (reset_cyc >= 0 && c == reset_cyc);
            sts_clear = (clear_cyc >= 0 && c == clear_cyc);
            @(posedge clk);
            #1 if (mismatch()) scn_fail++;
            if (s_valid && s_ready_r) di++;   // reference accepted the beat -> advance
            @(negedge clk);
        end
        $display("SCENARIO %s %s", name, (scn_fail == 0) ? "PASS" : "FAIL");
    endtask

    int pass_count;
    localparam int N_SCN = 6;

    initial begin
        pass_count = 0;
        //                  name                    margin cons_k len reset clear
        run_scn("lossless_backpressure", 2, 3, 80, -1, -1); if (scn_fail == 0) pass_count++;
        run_scn("watermark_telemetry",   3, 64, 56, -1, 34); if (scn_fail == 0) pass_count++;
        run_scn("fill_drain_order",      1, 2, 80, -1, -1); if (scn_fail == 0) pass_count++;
        run_scn("reset_midstream",       2, 3, 60, 30, -1); if (scn_fail == 0) pass_count++;
        run_scn("simul_in_out",          2, 2, 60, -1, -1); if (scn_fail == 0) pass_count++;
        run_scn("overflow_pressure",     0, 8, 50, -1, -1); if (scn_fail == 0) pass_count++;

        $display("SUMMARY hidden %0d/%0d", pass_count, N_SCN);
        $finish;
    end

    initial begin #800000; $display("SUMMARY hidden 0/%0d (timeout)", N_SCN); $finish; end
endmodule
