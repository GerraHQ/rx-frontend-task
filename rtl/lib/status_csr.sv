// status_csr — generic sticky status register (verified library block, provided
// correct). Each bit sets on its one-cycle (or level) event and holds until either a
// host write-to-clear strobe (clear_i) or reset. Provided complete; not an edit target.
module status_csr #(
    parameter int n_p = 2
) (
    input  logic           clk_i,
    input  logic           reset_i,    // synchronous, active high
    input  logic           clear_i,    // host write-to-clear strobe
    input  logic [n_p-1:0] event_i,    // per-bit set conditions
    output logic [n_p-1:0] sticky_o
);
    always_ff @(posedge clk_i) begin
        if (reset_i || clear_i) sticky_o <= '0;
        else                    sticky_o <= sticky_o | event_i;
    end
endmodule
