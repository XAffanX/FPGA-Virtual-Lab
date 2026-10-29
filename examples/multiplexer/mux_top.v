// Project D - Configurable 4-to-1 multiplexer on the Basys 3.
//
//   Data inputs (WIDTH = 3 bits each):
//     d0 = SW2..SW0    d1 = SW5..SW3    d2 = SW8..SW6    d3 = SW11..SW9
//   Select: SW15..SW14
//   Output: LED2..LED0
//   LED15..LED12 show which input is selected (one-hot).
module mux_top (
    input  wire [15:0] sw,
    output wire [15:0] led
);
    localparam WIDTH = 3;
    wire [1:0]       sel = sw[15:14];
    wire [WIDTH-1:0] y;

    mux4 #(.WIDTH(WIDTH)) u_mux (
        .d0(sw[2:0]), .d1(sw[5:3]), .d2(sw[8:6]), .d3(sw[11:9]),
        .sel(sel), .y(y)
    );

    assign led[WIDTH-1:0] = y;
    assign led[11:WIDTH]  = 0;
    assign led[15:12]     = 4'b0001 << sel;
endmodule
