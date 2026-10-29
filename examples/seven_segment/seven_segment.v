// Project E - Four-digit hexadecimal display.
// The 16 switches are shown as four hex digits (SW15..SW12 on the left digit).
// The digits are time-multiplexed: one anode is active (LOW) at a time.
// Holding BTNC lights the decimal point of the rightmost digit.
//
// REFRESH_BITS sets the multiplex rate: each digit is shown for
// 2^(REFRESH_BITS-2) clock cycles. Hardware designs typically use 18-20 bits
// (about 1-10 ms per digit at 100 MHz); 12 keeps the display steady in the
// simulator at Fast speed and above. Both values simulate correctly.
module seven_segment #(
    parameter REFRESH_BITS = 12
) (
    input  wire        clk,
    input  wire        btnC,
    input  wire [15:0] sw,
    output wire [6:0]  seg,
    output wire        dp,
    output reg  [3:0]  an
);
    reg [REFRESH_BITS-1:0] refresh = 0;
    always @(posedge clk)
        refresh <= refresh + 1'b1;

    wire [1:0] digit = refresh[REFRESH_BITS-1:REFRESH_BITS-2];
    reg  [3:0] nibble;

    always @(*) begin
        case (digit)
            2'd0:    begin an = 4'b1110; nibble = sw[3:0];   end
            2'd1:    begin an = 4'b1101; nibble = sw[7:4];   end
            2'd2:    begin an = 4'b1011; nibble = sw[11:8];  end
            default: begin an = 4'b0111; nibble = sw[15:12]; end
        endcase
    end

    hex_to_7seg u_dec (.hex(nibble), .seg(seg));

    assign dp = ~(btnC && digit == 2'd0);
endmodule
