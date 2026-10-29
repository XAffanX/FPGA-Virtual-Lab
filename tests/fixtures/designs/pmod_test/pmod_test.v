// Basys 3 Pmod test: JA[3:0] (JA1-JA4) outputs follow SW[3:0]; JA[7:4] (JA7-JA10) are inputs shown
// on LED[3:0]; JB[0] (JB1) toggles every clock cycle; JB[1] (JB2) input is shown on LED15.
module pmod_test (
    input  wire        clk,
    input  wire [15:0] sw,
    output wire [3:0]  JA_out,
    input  wire [3:0]  JA_in,
    output reg         JB_tog = 1'b0,
    input  wire        JB_in,
    output wire [15:0] led
);
    always @(posedge clk) JB_tog <= ~JB_tog;
    assign JA_out = sw[3:0];
    assign led = {JB_in, 11'd0, JA_in};
endmodule
