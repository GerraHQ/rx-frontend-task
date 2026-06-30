// visible_tb — directed sanity test shipped WITH the task (run via `make test`).
//
// It does NOT score the submission; it is a small, readable guide that fails on the
// shipped RTL and passes once the integration is correct. The hidden grader is stricter
// (a full cycle-accurate diff against a golden reference over more scenarios).
//
// Checks:
//   A. early backpressure: whenever almost_full_o is asserted, s_ready_o must be low.
//   B. watermark telemetry: after the watermark is hit, status_o[1] must be sticky-set.
//   C. data path: a well-behaved producer's bytes come out in order with no loss.
`timescale 1ns/1ps
module visible_tb;
    localparam int W = 8, D = 8, CW = $clog2(D + 1), K = 48;

    logic clk = 1'b0, reset = 1'b1;
    always #5 clk = ~clk;

    logic [W-1:0]  s_data;  logic s_valid, s_ready;
    logic [W-1:0]  m_data;  logic m_valid, m_ready;
    logic [CW-1:0] afull_margin; logic sts_clear;
    logic almost_full; logic [1:0] status; logic err; logic [1:0] level; logic [CW-1:0] count;

    rx_frontend #(.width_p(W), .depth_p(D)) dut (
        .clk_i(clk), .reset_i(reset),
        .s_data_i(s_data), .s_valid_i(s_valid), .s_ready_o(s_ready),
        .m_data_o(m_data), .m_valid_o(m_valid), .m_ready_i(m_ready),
        .afull_margin_i(afull_margin), .sts_clear_i(sts_clear),
        .almost_full_o(almost_full), .status_o(status), .err_o(err),
        .level_o(level), .count_o(count)
    );

    int errors = 0;
    task automatic chk(input bit cond, input string msg);
        if (!cond) begin $display("  FAIL: %s", msg); errors++; end
    endtask

    initial begin #200000; $display("TIMEOUT"); $finish; end

    int send_idx, recv_idx, t;
    logic [W-1:0] recv_mem [0:K-1];
    bit saw_almost_full, fire_pending;

    initial begin
        s_valid = 0; s_data = 0; m_ready = 0; afull_margin = 2; sts_clear = 0;
        repeat (3) @(posedge clk); reset <= 1'b0; @(posedge clk);

        send_idx = 0; recv_idx = 0; t = 0; saw_almost_full = 0;
        fork
            begin : producer
                while (send_idx < K) begin
                    @(negedge clk);
                    s_valid = 1'b1; s_data = send_idx[W-1:0];
                    fire_pending = (s_valid && s_ready);
                    @(posedge clk);
                    if (fire_pending) send_idx++;
                end
                @(negedge clk); s_valid = 1'b0;
            end
            begin : consumer
                while (recv_idx < K) begin
                    @(negedge clk);
                    m_ready = (t % 3 == 0);
                    // A. early-backpressure invariant
                    chk(!(almost_full && s_ready), "almost_full asserted but s_ready still high (no early backpressure)");
                    if (almost_full) saw_almost_full = 1;
                    if (m_valid && m_ready) begin recv_mem[recv_idx] = m_data; recv_idx++; end
                    @(posedge clk);
                    t++;
                end
                @(negedge clk); m_ready = 1'b0;
            end
        join

        // C. data path integrity
        for (int i = 0; i < K; i++)
            chk(recv_mem[i] == i[W-1:0], $sformatf("data out of order/lost at index %0d", i));
        chk(recv_idx == K, "not all beats made it through");
        chk(saw_almost_full, "watermark never engaged (test inconclusive)");

        // B. watermark telemetry sticky
        chk(status[1] == 1'b1, "watermark-hit status bit never set after backpressure");
        chk(err == 1'b0, "overflow/error flag set on a well-behaved run");

        if (errors == 0) $display("VISIBLE PASS");
        else             $display("VISIBLE FAIL (%0d issue(s))", errors);
        $finish;
    end
endmodule
