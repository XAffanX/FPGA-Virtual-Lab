// Basys 3 VGA test: 640x480 @ 60 Hz (25 MHz pixel enable from the 100 MHz clock).
// Eight vertical colour bars, plus a white 32x32 square at x = SW[7:0]*2, y = SW[15:8].
module vga_test (
    input  wire        clk,
    input  wire [15:0] sw,
    output reg  [3:0]  vgaRed,
    output reg  [3:0]  vgaGreen,
    output reg  [3:0]  vgaBlue,
    output reg         Hsync,
    output reg         Vsync
);
    reg [1:0] div = 0;
    reg [9:0] hc = 0, vc = 0;
    wire pix = (div == 2'd3);
    always @(posedge clk) begin
        div <= div + 1'b1;
        if (pix) begin
            if (hc == 799) begin hc <= 0; vc <= (vc == 524) ? 10'd0 : vc + 1'b1; end
            else hc <= hc + 1'b1;
        end
    end
    wire active = (hc < 640) && (vc < 480);
    wire [9:0] bx = {sw[7:0], 1'b0};
    wire [9:0] by = {2'b00, sw[15:8]};
    wire box = (hc >= bx) && (hc < bx + 32) && (vc >= by) && (vc < by + 32);
    wire [2:0] bar = hc[9:7] ^ {2'b00, hc[9]};    // 0..4 for 0..639 -> 5 bars + variants
    always @(posedge clk) begin
        Hsync <= ~((hc >= 656) && (hc < 752));     // negative sync
        Vsync <= ~((vc >= 490) && (vc < 492));
        if (!active) {vgaRed, vgaGreen, vgaBlue} <= 12'h000;
        else if (box) {vgaRed, vgaGreen, vgaBlue} <= 12'hFFF;
        else case (hc[9:6])
            4'd0: {vgaRed, vgaGreen, vgaBlue} <= 12'hF00;
            4'd1: {vgaRed, vgaGreen, vgaBlue} <= 12'h0F0;
            4'd2: {vgaRed, vgaGreen, vgaBlue} <= 12'h00F;
            4'd3: {vgaRed, vgaGreen, vgaBlue} <= 12'hFF0;
            4'd4: {vgaRed, vgaGreen, vgaBlue} <= 12'h0FF;
            4'd5: {vgaRed, vgaGreen, vgaBlue} <= 12'hF0F;
            4'd6: {vgaRed, vgaGreen, vgaBlue} <= 12'h888;
            4'd7: {vgaRed, vgaGreen, vgaBlue} <= 12'h444;
            4'd8: {vgaRed, vgaGreen, vgaBlue} <= 12'hF80;
            default: {vgaRed, vgaGreen, vgaBlue} <= 12'h08F;
        endcase
    end
endmodule
