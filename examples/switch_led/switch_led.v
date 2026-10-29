// Project A - Switch-to-LED
// Each slide switch drives the LED directly above it.
module switch_led (
    input  wire [15:0] sw,
    output wire [15:0] led
);
    assign led = sw;
endmodule
