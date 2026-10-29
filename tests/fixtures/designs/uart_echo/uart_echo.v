// Basys 3 UART test (115200 baud, 8N1): echoes every received byte, shows the last byte on
// LED[7:0], and sends "HI\r\n" when BTNC is pressed.
module uart_echo (
    input  wire       clk,
    input  wire       RsRx,
    output wire       RsTx,
    input  wire       btnC,
    output wire [15:0] led
);
    localparam integer CPB = 868;            // 100 MHz / 115200

    // ---------------- receiver
    reg [1:0]  rx_sync = 2'b11;
    reg        rx_busy = 1'b0;
    reg [15:0] rx_cnt = 0;
    reg [3:0]  rx_bit = 0;
    reg [7:0]  rx_sh = 0;
    reg [7:0]  last = 0;
    reg        rx_done = 1'b0;
    always @(posedge clk) begin
        rx_sync <= {rx_sync[0], RsRx};
        rx_done <= 1'b0;
        if (!rx_busy) begin
            if (!rx_sync[1]) begin rx_busy <= 1'b1; rx_cnt <= CPB + CPB / 2; rx_bit <= 0; end
        end else if (rx_cnt != 0) begin
            rx_cnt <= rx_cnt - 1'b1;
        end else if (rx_bit < 8) begin
            rx_sh  <= {rx_sync[1], rx_sh[7:1]};
            rx_bit <= rx_bit + 1'b1;
            rx_cnt <= CPB - 1;
        end else begin
            rx_busy <= 1'b0;                  // stop bit
            last    <= rx_sh;
            rx_done <= 1'b1;
        end
    end

    // ---------------- transmitter with a tiny queue
    reg [7:0]  q [0:7];
    reg [2:0]  q_head = 0, q_tail = 0;
    reg        tx_busy = 1'b0;
    reg [15:0] tx_cnt = 0;
    reg [3:0]  tx_bit = 0;
    reg [9:0]  tx_sh = 10'h3FF;
    reg [2:0]  b_sync = 0;
    wire       press = b_sync[1] & ~b_sync[2];
    always @(posedge clk) begin
        b_sync <= {b_sync[1:0], btnC};
        if (rx_done) begin q[q_tail] <= rx_sh; q_tail <= q_tail + 1'b1; end
        else if (press) begin
            q[q_tail] <= "H"; q[q_tail + 3'd1] <= "I"; q[q_tail + 3'd2] <= 8'h0D; q[q_tail + 3'd3] <= 8'h0A;
            q_tail <= q_tail + 3'd4;
        end
        if (!tx_busy) begin
            if (q_head != q_tail) begin
                tx_sh <= {1'b1, q[q_head], 1'b0};
                q_head <= q_head + 1'b1;
                tx_busy <= 1'b1; tx_cnt <= CPB - 1; tx_bit <= 0;
            end
        end else if (tx_cnt != 0) begin
            tx_cnt <= tx_cnt - 1'b1;
        end else begin
            tx_sh <= {1'b1, tx_sh[9:1]};
            tx_cnt <= CPB - 1;
            if (tx_bit == 9) tx_busy <= 1'b0;
            tx_bit <= tx_bit + 1'b1;
        end
    end
    assign RsTx = tx_busy ? tx_sh[0] : 1'b1;
    assign led  = {8'h00, last};
endmodule
