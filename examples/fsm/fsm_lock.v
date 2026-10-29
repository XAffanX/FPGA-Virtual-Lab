// Project F - Finite-state machine: pushbutton combination lock.
//
// Enter the code  BTNU, BTNL, BTNR, BTND  to unlock.
//   LED3..LED0 : progress (one LED per correct button)
//   LED15      : unlocked
//   LED14      : wrong button entered (error state)
//   BTNC       : reset / lock again
// The rightmost seven-segment digit shows the current state number.
module fsm_lock (
    input  wire        clk,
    input  wire        btnC,
    input  wire        btnU,
    input  wire        btnL,
    input  wire        btnR,
    input  wire        btnD,
    output reg  [15:0] led,
    output wire [6:0]  seg,
    output wire        dp,
    output wire [3:0]  an
);
    localparam [2:0] S_IDLE = 3'd0, S_GOT1 = 3'd1, S_GOT2 = 3'd2,
                     S_GOT3 = 3'd3, S_OPEN = 3'd4, S_ERROR = 3'd5;

    wire pu, pl, pr, pd;
    button_sync su (.clk(clk), .btn(btnU), .pressed(pu));
    button_sync sl (.clk(clk), .btn(btnL), .pressed(pl));
    button_sync sr (.clk(clk), .btn(btnR), .pressed(pr));
    button_sync sd (.clk(clk), .btn(btnD), .pressed(pd));

    wire any = pu | pl | pr | pd;
    reg [2:0] state = S_IDLE;
    reg [2:0] next;

    always @(*) begin
        next = state;
        if (any) begin
            case (state)
                S_IDLE:  next = pu ? S_GOT1 : S_ERROR;
                S_GOT1:  next = pl ? S_GOT2 : S_ERROR;
                S_GOT2:  next = pr ? S_GOT3 : S_ERROR;
                S_GOT3:  next = pd ? S_OPEN : S_ERROR;
                default: next = state;           // OPEN and ERROR wait for BTNC
            endcase
        end
    end

    always @(posedge clk) begin
        if (btnC) state <= S_IDLE;
        else      state <= next;
    end

    always @(*) begin
        led = 16'd0;
        case (state)
            S_GOT1:  led[0]   = 1'b1;
            S_GOT2:  led[1:0] = 2'b11;
            S_GOT3:  led[2:0] = 3'b111;
            S_OPEN:  begin led[3:0] = 4'b1111; led[15] = 1'b1; end
            S_ERROR: led[14]  = 1'b1;
            default: ;
        endcase
    end

    // Single digit, so no multiplexing: digit 0 always enabled.
    hex_to_7seg u_dec (.hex({1'b0, state}), .seg(seg));
    assign an = 4'b1110;
    assign dp = 1'b1;
endmodule
