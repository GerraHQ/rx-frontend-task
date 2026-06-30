// rx_frontend — GOLDEN reference, module renamed for the diff bench (root-owned; never shipped to the agent).
// Link ingress front-end. Composes a vendored PULP `fifo_v3` library FIFO and a
// generic sticky `status_csr`, and integrates the control logic on top: the
// watermark (almost_full) with one-cycle deassert hysteresis, early backpressure
// off that watermark, sticky overflow, cross-block status aggregation, the true
// occupancy count (fifo_v3's usage_o truncates at full), and a latch-free fill
// encode. The library blocks are correct and fixed; this integration is the work.
module rx_frontend_ref #(
    parameter int width_p        = 8,
    parameter int depth_p        = 8,
    parameter int count_width_lp = $clog2(depth_p + 1)
) (
    input  logic                      clk_i,
    input  logic                      reset_i,        // synchronous, active high

    // upstream ingress (producer)
    input  logic [width_p-1:0]        s_data_i,
    input  logic                      s_valid_i,
    output logic                      s_ready_o,

    // downstream egress (consumer)
    output logic [width_p-1:0]        m_data_o,
    output logic                      m_valid_o,
    input  logic                      m_ready_i,

    // control / status
    input  logic [count_width_lp-1:0] afull_margin_i, // watermark margin, in free entries
    input  logic                      sts_clear_i,     // host write-to-clear for status
    output logic                      almost_full_o,
    output logic [1:0]                status_o,        // {watermark_hit, overflow} sticky
    output logic                      err_o,
    output logic [1:0]                level_o,         // coarse fill encode
    output logic [count_width_lp-1:0] count_o
);
    localparam int addr_w_lp = (depth_p > 1) ? $clog2(depth_p) : 1;

    // ---- stage 1: registered input beat (one cycle of in-flight latency) ----
    logic [width_p-1:0] in_data_r;
    logic               in_valid_r;

    // ---- fifo_v3 status ----
    logic                 fifo_full, fifo_empty;
    logic [addr_w_lp-1:0] fifo_usage;

    // Offer the held beat whenever stage 1 has one; fifo_v3 only writes when not full.
    wire fifo_push   = in_valid_r;
    wire fifo_pop    = m_valid_o && m_ready_i;
    wire in_drains   = in_valid_r && !fifo_full;   // beat accepted by the store this cycle
    wire in_can_load = !in_valid_r || in_drains;

    // ---- true occupancy: usage_o truncates at full, so recover the count ----
    assign count_o = fifo_full ? count_width_lp'(depth_p) : count_width_lp'(fifo_usage);
    wire [count_width_lp-1:0] free_entries = count_width_lp'(depth_p) - count_o;
    wire at_margin = (free_entries <= afull_margin_i);

    // ---- watermark with one-cycle deassert hysteresis ----
    logic almost_full_r;
    assign almost_full_o = at_margin || almost_full_r;

    // ---- early backpressure off the watermark (not the raw full flag) ----
    assign s_ready_o = in_can_load && !almost_full_o;
    wire s_fire = s_valid_i && s_ready_o;

    // ---- sticky overflow: a held beat offered to a full store is dropped ----
    wire overflow_event = in_valid_r && fifo_full;

    always_ff @(posedge clk_i) begin
        if (reset_i) begin
            in_valid_r    <= 1'b0;
            in_data_r     <= '0;
            almost_full_r <= 1'b0;
        end else begin
            if (s_fire) begin
                in_valid_r <= 1'b1;
                in_data_r  <= s_data_i;
            end else if (in_drains) begin
                in_valid_r <= 1'b0;
            end
            almost_full_r <= at_margin;
        end
    end

    // ---- stage 2: vendored PULP fifo_v3 elastic store ----
    fifo_v3 #(.DATA_WIDTH(width_p), .DEPTH(depth_p)) fifo (
        .clk_i,
        .rst_ni    (~reset_i),
        .flush_i   (1'b0),
        .testmode_i(1'b0),
        .full_o    (fifo_full),
        .empty_o   (fifo_empty),
        .usage_o   (fifo_usage),
        .data_i    (in_data_r),
        .push_i    (fifo_push),
        .data_o    (m_data_o),
        .pop_i     (fifo_pop)
    );
    assign m_valid_o = !fifo_empty;

    // ---- stage 3: sticky status aggregation ----
    status_csr #(.n_p(2)) sts (
        .clk_i,
        .reset_i,
        .clear_i  (sts_clear_i),
        .event_i  ({almost_full_o, overflow_event}),
        .sticky_o (status_o)
    );
    assign err_o = status_o[0];

    // ---- top-level coarse fill encode (latch-free) ----
    always_comb begin
        case ({fifo_full, fifo_empty})
            2'b01:   level_o = 2'd0;                        // empty
            2'b10:   level_o = 2'd3;                        // full
            2'b00:   level_o = almost_full_o ? 2'd2 : 2'd1; // occupied / backpressuring
            default: level_o = 2'd1;                        // {full,empty} unreachable
        endcase
    end
endmodule
