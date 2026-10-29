// Project B - Four-bit binary counter
// The counter increments on every rising edge of the 100 MHz board clock and is
// shown on LED3..LED0. BTNC is a synchronous reset.
//
// In the simulator use "Step Clock" or the Slow/Normal speeds to watch it count.
// (On a physical board you would divide the clock first, e.g. show count[27:24].)
module counter (
    input  wire       clk,
    input  wire       btnC,
    output wire [3:0] led
);
    reg [3:0] count = 4'd0;

    always @(posedge clk) begin
        if (btnC)
            count <= 4'd0;
        else
            count <= count + 4'd1;
    end

    assign led = count;
endmodule
