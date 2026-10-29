// Nexys 4 DDR test design: counter on LED[3:0] (CPU_RESETN, active low, resets it),
// LED[15:8] = SW[15:8], RGB LED16 = SW[2:0], BTNC lights LED[4],
// 8-digit display shows digit index k on digit k (digit 0 rightmost), multiplexed.
module nexys_demo (
    input  wire        CLK100MHZ,
    input  wire        CPU_RESETN,
    input  wire        BTNC,
    input  wire [15:0] SW,
    output wire [15:0] LED,
    output wire        LED16_R, LED16_G, LED16_B,
    output wire        CA, CB, CC, CD, CE, CF, CG, DP,
    output reg  [7:0]  AN
);
    reg [3:0]  count = 4'd0;
    reg [9:0]  refresh = 10'd0;
    always @(posedge CLK100MHZ) begin
        if (!CPU_RESETN) count <= 4'd0;
        else             count <= count + 4'd1;
        refresh <= refresh + 1'b1;
    end
    assign LED[3:0]  = count;
    assign LED[4]    = BTNC;
    assign LED[7:5]  = 3'b000;
    assign LED[15:8] = SW[15:8];
    assign {LED16_R, LED16_G, LED16_B} = SW[2:0];

    wire [2:0] digit = refresh[9:7];
    reg  [6:0] seg;                  // gfedcba, active low
    always @(*) begin
        AN = ~(8'b1 << digit);
        case (digit)
            3'd0: seg = 7'b1000000;  3'd1: seg = 7'b1111001;
            3'd2: seg = 7'b0100100;  3'd3: seg = 7'b0110000;
            3'd4: seg = 7'b0011001;  3'd5: seg = 7'b0010010;
            3'd6: seg = 7'b0000010;  default: seg = 7'b1111000;
        endcase
    end
    assign {CG, CF, CE, CD, CC, CB, CA} = seg;
    assign DP = 1'b1;
endmodule
