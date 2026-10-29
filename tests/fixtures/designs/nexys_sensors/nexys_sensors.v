// Nexys 4 DDR on-board sensor test.
//  * ADT7420 temperature sensor (I2C, address 0x4B): reads registers 0x00/0x01 repeatedly.
//  * ADXL362 accelerometer (SPI mode 0): sets POWER_CTL=0x02 (measure), then reads XDATA (0x08).
//  * PDM microphone: M_CLK = 2.5 MHz, counts ones in each 255-sample window.
//  Display: digits 3..0 = temperature register {MSB, LSB} in hex, digits 5..4 = XDATA,
//           digits 7..6 = microphone density. LED[15:8] = XDATA, LED[7:0] = temperature MSB.
module nexys_sensors (
    input  wire        CLK100MHZ,
    output reg         TMP_SCL = 1'b1,
    inout  wire        TMP_SDA,
    output reg         ACL_SCLK = 1'b0,
    output reg         ACL_MOSI = 1'b0,
    output reg         ACL_CSN  = 1'b1,
    input  wire        ACL_MISO,
    output reg         M_CLK = 1'b0,
    input  wire        M_DATA,
    output wire        M_LRSEL,
    output wire [15:0] LED,
    output wire        CA, CB, CC, CD, CE, CF, CG, DP,
    output reg  [7:0]  AN
);
    assign M_LRSEL = 1'b0;

    // ------------------------------------------------------------ I2C master (200 kHz bits)
    reg  [6:0]  i2c_div = 0;
    wire        i2c_q   = (i2c_div == 124);            // quarter-bit tick
    reg  [1:0]  q = 0;
    reg  [5:0]  sym = 0;
    reg         sda_rel = 1'b1;                         // 1 = release (pulled up), 0 = drive low
    reg  [15:0] t_sh = 0;
    reg  [15:0] temp = 0;
    reg  [11:0] gap = 0;
    assign TMP_SDA = sda_rel ? 1'bz : 1'b0;

    // symbol kinds: 0 start, 1 restart, 2 stop, 3 write bit, 4 release/sample, 5 master ACK, 6 master NACK
    reg [2:0] kind; reg wbit;
    localparam [7:0] AW = 8'h96, PTR = 8'h00, AR = 8'h97;
    always @(*) begin
        kind = 3'd4; wbit = 1'b1;
        if (sym == 0)                    kind = 3'd0;
        else if (sym <= 8)  begin kind = 3'd3; wbit = AW[8 - sym];  end
        else if (sym == 9)               kind = 3'd4;
        else if (sym <= 17) begin kind = 3'd3; wbit = PTR[17 - sym]; end
        else if (sym == 18)              kind = 3'd4;
        else if (sym == 19)              kind = 3'd1;
        else if (sym <= 27) begin kind = 3'd3; wbit = AR[27 - sym]; end
        else if (sym == 28)              kind = 3'd4;
        else if (sym <= 36)              kind = 3'd4;           // read MSB bits
        else if (sym == 37)              kind = 3'd5;
        else if (sym <= 45)              kind = 3'd4;           // read LSB bits
        else if (sym == 46)              kind = 3'd6;
        else                             kind = 3'd2;           // 47: stop
    end
    always @(posedge CLK100MHZ) begin
        i2c_div <= i2c_q ? 7'd0 : i2c_div + 1'b1;
        if (i2c_q) begin
            if (gap != 0) gap <= gap - 1'b1;
            else begin
                case (kind)
                    3'd0: begin TMP_SCL <= (q != 3); sda_rel <= (q < 2); end
                    3'd1: begin TMP_SCL <= (q == 1 || q == 2); sda_rel <= (q < 2); end
                    3'd2: begin TMP_SCL <= (q != 0); sda_rel <= (q == 3); end
                    3'd3: begin TMP_SCL <= (q == 1 || q == 2); sda_rel <= wbit; end
                    3'd5: begin TMP_SCL <= (q == 1 || q == 2); sda_rel <= 1'b0; end
                    3'd6: begin TMP_SCL <= (q == 1 || q == 2); sda_rel <= 1'b1; end
                    default: begin TMP_SCL <= (q == 1 || q == 2); sda_rel <= 1'b1;
                        if (q == 2 && ((sym >= 29 && sym <= 36) || (sym >= 38 && sym <= 45)))
                            t_sh <= {t_sh[14:0], TMP_SDA};
                    end
                endcase
                q <= q + 1'b1;
                if (q == 3) begin
                    if (sym == 47) begin sym <= 0; temp <= t_sh; gap <= 12'd400; end
                    else sym <= sym + 1'b1;
                end
            end
        end
    end

    // ------------------------------------------------------------ SPI master (1 MHz)
    reg  [5:0]  spi_div = 0;
    wire        spi_h = (spi_div == 49);                 // half-bit tick
    reg  [5:0]  sbit = 0;                                 // bit index within the transaction
    reg         phase = 0;                                // 0 = SCLK low half, 1 = high half
    reg         init_done = 0;
    reg  [23:0] tx = 0;
    reg  [7:0]  rx = 0, xdata = 0;
    reg  [9:0]  sgap = 10'd10;
    always @(posedge CLK100MHZ) begin
        spi_div <= spi_h ? 6'd0 : spi_div + 1'b1;
        if (spi_h) begin
            if (sgap != 0) begin
                sgap <= sgap - 1'b1; ACL_CSN <= 1'b1; ACL_SCLK <= 1'b0;
                if (sgap == 1) begin
                    tx <= init_done ? {8'h0B, 8'h08, 8'h00} : {8'h0A, 8'h2D, 8'h02};
                    sbit <= 0; phase <= 0; ACL_CSN <= 1'b0;
                end
            end else if (!phase) begin
                ACL_SCLK <= 1'b0; ACL_MOSI <= tx[23 - sbit]; phase <= 1'b1;
            end else begin
                ACL_SCLK <= 1'b1; phase <= 1'b0;
                if (sbit >= 16) rx <= {rx[6:0], ACL_MISO};
                if (sbit == 23) begin
                    sgap <= 10'd20;
                    if (init_done) xdata <= {rx[6:0], ACL_MISO};
                    init_done <= 1'b1;
                end else sbit <= sbit + 1'b1;
            end
        end
    end

    // ------------------------------------------------------------ PDM microphone
    reg [4:0] m_div = 0;
    reg [7:0] m_n = 0, m_ones = 0, density = 0;
    always @(posedge CLK100MHZ) begin
        m_div <= (m_div == 19) ? 5'd0 : m_div + 1'b1;
        if (m_div == 19) begin
            M_CLK <= ~M_CLK;
            if (!M_CLK) begin                              // rising edge of M_CLK
                if (m_n == 254) begin density <= m_ones + M_DATA; m_ones <= 0; m_n <= 0; end
                else begin m_ones <= m_ones + M_DATA; m_n <= m_n + 1'b1; end
            end
        end
    end

    // ------------------------------------------------------------ outputs
    assign LED = {xdata, temp[15:8]};
    reg [9:0] refresh = 0;
    always @(posedge CLK100MHZ) refresh <= refresh + 1'b1;
    wire [2:0] dig = refresh[9:7];
    reg  [3:0] nib;
    always @(*) begin
        AN = ~(8'b1 << dig);
        case (dig)
            3'd0: nib = temp[3:0];   3'd1: nib = temp[7:4];   3'd2: nib = temp[11:8]; 3'd3: nib = temp[15:12];
            3'd4: nib = xdata[3:0];  3'd5: nib = xdata[7:4];  3'd6: nib = density[3:0]; default: nib = density[7:4];
        endcase
    end
    reg [6:0] seg;
    always @(*) case (nib)
        4'h0: seg = 7'b1000000; 4'h1: seg = 7'b1111001; 4'h2: seg = 7'b0100100; 4'h3: seg = 7'b0110000;
        4'h4: seg = 7'b0011001; 4'h5: seg = 7'b0010010; 4'h6: seg = 7'b0000010; 4'h7: seg = 7'b1111000;
        4'h8: seg = 7'b0000000; 4'h9: seg = 7'b0010000; 4'hA: seg = 7'b0001000; 4'hB: seg = 7'b0000011;
        4'hC: seg = 7'b1000110; 4'hD: seg = 7'b0100001; 4'hE: seg = 7'b0000110; default: seg = 7'b0001110;
    endcase
    assign {CG, CF, CE, CD, CC, CB, CA} = seg;
    assign DP = 1'b1;
endmodule
