// Two-flip-flop synchroniser plus rising-edge detector for a pushbutton.
// (The simulator has no contact bounce, so no debouncer is needed here;
//  on real hardware add one.)
module button_sync (
    input  wire clk,
    input  wire btn,
    output wire pressed      // one clock pulse per press
);
    reg [2:0] s = 3'b000;
    always @(posedge clk)
        s <= {s[1:0], btn};
    assign pressed = s[1] & ~s[2];
endmodule
