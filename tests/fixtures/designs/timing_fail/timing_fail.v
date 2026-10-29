// Basys 3 timing test: 16 dependent rounds of v = (v + (v << 3)) ^ (v >> 5) between two registers.
// Each round needs the previous result, so synthesis cannot rebalance it: the path is several
// times longer than the 10 ns clock period and the design FAILS timing at 100 MHz.
// Functionally (no delays) LED = acc[31:16] ^ acc[15:0] with acc = f(a) of the previous cycle;
// with real delays the captured value is wrong, exactly as it would be on the real board.
module timing_fail (
    input  wire        clk,
    output wire [15:0] led
);
    reg [31:0] a = 32'd0, acc = 32'd0;
    always @(posedge clk) a <= a + 32'h01030507;

    function [31:0] round(input [31:0] v);
        round = (v + (v << 3)) ^ (v >> 5);
    endfunction

    wire [31:0] s = round(round(round(round(round(round(round(round(
                    round(round(round(round(round(round(round(round(a))))))))))))))));
    always @(posedge clk) acc <= s;
    assign led = acc[31:16] ^ acc[15:0];
endmodule
