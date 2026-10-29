// Project C - Two-bit adder / subtractor built from two full adders.
//
//   SW1..SW0   = A            SW3..SW2 = B
//   SW15       = mode (0 = A + B, 1 = A - B)
//   LED1..LED0 = result       LED2 = carry out (add) / no-borrow (subtract)
//   LED3       = signed overflow
//   LED15      = mode indicator
module adder_subtractor (
    input  wire [15:0] sw,
    output wire [15:0] led
);
    wire [1:0] a   = sw[1:0];
    wire [1:0] b   = sw[3:2];
    wire       sub = sw[15];
    wire [1:0] bx  = b ^ {2{sub}};     // invert B for subtraction (two's complement)
    wire [1:0] s;
    wire       c1, c2;

    full_adder fa0 (.a(a[0]), .b(bx[0]), .cin(sub), .sum(s[0]), .cout(c1));
    full_adder fa1 (.a(a[1]), .b(bx[1]), .cin(c1),  .sum(s[1]), .cout(c2));

    assign led[1:0]  = s;
    assign led[2]    = c2;
    assign led[3]    = c1 ^ c2;         // signed overflow
    assign led[14:4] = 11'd0;
    assign led[15]   = sub;
endmodule
