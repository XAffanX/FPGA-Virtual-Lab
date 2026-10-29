// Test design for active-low reset: 4-bit counter with synchronous active-low reset rst_n (SW15).
module counter_rstn (
    input  wire       clk,
    input  wire       rst_n,
    output wire [3:0] led
);
    reg [3:0] count = 4'd0;
    always @(posedge clk) begin
        if (!rst_n) count <= 4'd0;
        else        count <= count + 4'd1;
    end
    assign led = count;
endmodule
