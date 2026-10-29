// Parameterised 4-to-1 multiplexer.
module mux4 #(
    parameter WIDTH = 1
) (
    input  wire [WIDTH-1:0] d0,
    input  wire [WIDTH-1:0] d1,
    input  wire [WIDTH-1:0] d2,
    input  wire [WIDTH-1:0] d3,
    input  wire [1:0]       sel,
    output reg  [WIDTH-1:0] y
);
    always @(*) begin
        case (sel)
            2'd0:    y = d0;
            2'd1:    y = d1;
            2'd2:    y = d2;
            default: y = d3;
        endcase
    end
endmodule
