"""Board peripherals simulated next to the design: USB-UART, VGA monitor and the Nexys 4 DDR sensors.

Each peripheral is a small Verilog model placed in the harness (see harness.HarnessExtras) when the
design actually uses its pins, plus a Python side that configures it (harness registers, op 4) and
decodes what it reports (``@@FVL U``/``P``/``F`` lines). Pmod pins need no model: they are ordinary
board pins (inputs are toggled, outputs shown).

Harness registers (fvl_preg):
  0 UART clock cycles per bit     1 UART byte to send (write = enqueue)
  2 ADT7420 temperature register (16-bit, 13-bit mode: degC*16 << 3)
  3/4/5 ADXL362 X/Y/Z acceleration in mg (signed)
  6 microphone amplitude (0..1000 = 0..100 % full scale)    7 microphone tone frequency in Hz
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.board.board import Board
from app.mapping.mapping import BitRef, ResolvedMapping

from .harness import HarnessExtras, output_layout

REG_UART_CPB, REG_UART_TX, REG_TEMP, REG_ACC_X, REG_ACC_Y, REG_ACC_Z, REG_MIC_AMP, REG_MIC_FREQ = range(8)
CLOCK_HZ = 100_000_000
VGA_W, VGA_H = 640, 480


def temp_register(celsius: float) -> int:
    """ADT7420 temperature register value (13-bit mode, 0.0625 degC/LSB, left-aligned)."""
    raw = int(round(celsius * 16)) & 0x1FFF
    return (raw << 3) & 0xFFFF


@dataclass
class VgaState:
    frame: bytearray = field(default_factory=lambda: bytearray(VGA_W * VGA_H * 3))
    frames: int = 0               # completed frames (vsync pulses seen)
    lines: int = 0                # lines per frame measured at the last vsync
    hperiod: int = 0              # hsync period in clock cycles
    divider: int = 0              # clock cycles per pixel
    changed: bool = False
    last_signal_cycle: int = -1
    _x: int = 0
    _y: int = -1
    _rgb: bytes = b"\x00\x00\x00"

    @property
    def timing_text(self) -> str:
        if not self.hperiod:
            return "receiving picture (waiting for the first vertical sync)" if self._y >= 0 else "no signal"
        pix = CLOCK_HZ / self.divider / 1e6 if self.divider else 0
        hfreq = CLOCK_HZ / self.hperiod / 1e3
        std = "640x480 @ 60 Hz" if self.divider and 790 <= self.hperiod / self.divider <= 810 and \
            515 <= self.lines <= 535 else "non-standard timing (shown as 640x480)"
        return (f"{std} · line {self.hperiod} cycles ({hfreq:.2f} kHz) · {self.lines} lines · pixel clock "
                f"{pix:.1f} MHz · frame {self.frames}")

    def _fill(self, y: int, x0: int, x1: int, rgb: bytes):
        if 0 <= y < VGA_H and x1 > x0:
            x0, x1 = max(0, x0), min(VGA_W, x1)
            i = (y * VGA_W + x0) * 3
            self.frame[i:i + (x1 - x0) * 3] = rgb * (x1 - x0)

    def pixel(self, x: int, y: int, color12: int):
        if y != self._y:
            self._fill(self._y, self._x, VGA_W, self._rgb)       # finish the previous line
            self._y, self._x = y, 0
        self._fill(y, self._x, x, self._rgb)
        r, g, b = (color12 >> 8) & 0xF, (color12 >> 4) & 0xF, color12 & 0xF
        self._rgb = bytes((r * 17, g * 17, b * 17))
        self._x = x
        self.changed = True

    def frame_start(self, lines: int, hperiod: int, divider: int):
        self._fill(self._y, self._x, VGA_W, self._rgb)
        self._y, self._x, self._rgb = -1, 0, b"\x00\x00\x00"
        self.frames += 1
        self.lines, self.hperiod, self.divider = lines, hperiod, divider
        self.changed = True


def _bit(o: BitRef) -> str:
    return f"fvl_o{o.port}[{o.pos}]"


class Peripherals:
    def __init__(self, board: Board, rm: ResolvedMapping):
        self.board = board
        self.rm = rm
        out = rm.outputs
        inp = rm.inputs
        self.uart_tx = out.get("UART_TX")                 # design -> PC
        self.uart_rx = inp.get("UART_RX", [])             # PC -> design
        self.vga = {k: out[k] for k in out if board.resources[k].kind == "vga"}
        self.has_vga = "VGA_HS" in self.vga and "VGA_VS" in self.vga
        self.tmp = "TMP_SCL" in out and "TMP_SDA" in rm.inouts
        self.acl = all(k in out for k in ("ACL_SCLK", "ACL_MOSI", "ACL_CSN")) and "ACL_MISO" in inp
        self.mic = "M_CLK" in out and "M_DATA" in inp
        self.uart_bytes: list[int] = []
        self.uart_errors = 0
        self.vga_state = VgaState()

    # ------------------------------------------------------------------ what is present
    @property
    def has_uart(self) -> bool:
        return self.uart_tx is not None or bool(self.uart_rx)

    @property
    def has_sensors(self) -> bool:
        return self.tmp or self.acl or self.mic

    def summary(self) -> list[str]:
        s = []
        if self.has_uart:
            s.append("USB-UART (" + ", ".join(x for x, ok in (("TX", self.uart_tx is not None),
                                                               ("RX", bool(self.uart_rx))) if ok) + ")")
        if self.has_vga:
            s.append(f"VGA ({len(self.vga) - 2} colour pins)")
        s += [n for n, ok in (("ADT7420 temperature sensor", self.tmp), ("ADXL362 accelerometer", self.acl),
                              ("PDM microphone", self.mic)) if ok]
        return s

    def initial_writes(self, baud: int, temp_c: float, accel: tuple[int, int, int], mic: tuple[int, int]):
        w = [(REG_UART_CPB, max(2, round(CLOCK_HZ / baud))), (REG_TEMP, temp_register(temp_c))]
        w += [(REG_ACC_X + i, v & 0xFFFFFFFF) for i, v in enumerate(accel)]
        w += [(REG_MIC_AMP, mic[0]), (REG_MIC_FREQ, mic[1])]
        return w

    # ------------------------------------------------------------------ harness code
    def extras(self) -> HarnessExtras:
        ex = HarnessExtras()
        if self.uart_tx is not None:
            ex.code.append(UART_MONITOR.replace("TXBIT", _bit(self.uart_tx)))
        if self.uart_rx:
            ex.code.append(UART_DRIVER)
            for ref in self.uart_rx:
                ex.overrides.setdefault(ref.port, []).append((ref.pos, "fvl_urx_line"))
        if self.has_vga:
            def bits(prefix):
                return ", ".join(_bit(self.vga[f"{prefix}{i}"]) if f"{prefix}{i}" in self.vga else "1'b0"
                                 for i in (3, 2, 1, 0))
            ex.code.append(VGA_MONITOR.replace("HSBIT", _bit(self.vga["VGA_HS"])).replace("VSBIT", _bit(self.vga["VGA_VS"]))
                           .replace("RBITS", bits("VGA_R")).replace("GBITS", bits("VGA_G")).replace("BBITS", bits("VGA_B")))
            # VGA pins change every pixel: keep them out of the LED/display event stream.
            offsets = {l.port_index: l.offset for l in output_layout(self.rm.ports)}
            width = sum(l.width for l in output_layout(self.rm.ports))
            mask = (1 << width) - 1
            for ref in self.vga.values():
                mask &= ~(1 << (offsets[ref.port] + ref.pos))
            ex.event_mask = mask
        if self.tmp:
            sda = self.rm.inouts["TMP_SDA"]
            ex.code.append(ADT7420.replace("SCLBIT", _bit(self.rm.outputs["TMP_SCL"])).replace("SDABIT", _bit(sda)))
            ex.inout_drivers.setdefault(sda.port, []).append((sda.pos, "fvl_tmp_low ? 1'b0 : 1'bz"))
        for rid in ("TMP_INT", "TMP_CT"):                     # open-drain outputs of the sensor: idle high
            for ref in self.rm.inputs.get(rid, []):
                ex.overrides.setdefault(ref.port, []).append((ref.pos, "1'b1"))
        if self.acl:
            ex.code.append(ADXL362.replace("SCLKBIT", _bit(self.rm.outputs["ACL_SCLK"]))
                           .replace("MOSIBIT", _bit(self.rm.outputs["ACL_MOSI"]))
                           .replace("CSNBIT", _bit(self.rm.outputs["ACL_CSN"])))
            for ref in self.rm.inputs["ACL_MISO"]:
                ex.overrides.setdefault(ref.port, []).append((ref.pos, "fvl_acl_miso"))
        for rid in ("ACL_INT1", "ACL_INT2"):
            for ref in self.rm.inputs.get(rid, []):
                ex.overrides.setdefault(ref.port, []).append((ref.pos, "1'b0"))
        if self.mic:
            ex.code.append(MIC.replace("MCLKBIT", _bit(self.rm.outputs["M_CLK"])))
            for ref in self.rm.inputs["M_DATA"]:
                ex.overrides.setdefault(ref.port, []).append((ref.pos, "fvl_mic_data"))
        return ex

    # ------------------------------------------------------------------ reports from the models
    def handle(self, lines: list[str]) -> None:
        vga = self.vga_state
        for line in lines:
            kind = line[:1]
            if kind == "U":
                self.uart_bytes.append(int(line[2:4], 16))
            elif kind == "u":                                  # framing error (stop bit low)
                self.uart_bytes.append(int(line[2:4], 16))
                self.uart_errors += 1
            elif kind == "P":
                _, x, y, c = line.split()
                vga.pixel(int(x), int(y), int(c, 16))
            elif kind == "F":
                _, n, hp, dv = line.split()
                vga.frame_start(int(n), int(hp), int(dv))

    def take_uart(self) -> list[int]:
        out, self.uart_bytes = self.uart_bytes, []
        return out


# ====================================================================== Verilog models
UART_MONITOR = r"""
  // ---- USB-UART: decode bytes the design transmits (8N1, LSB first) ----
  reg fvl_ut_busy = 1'b0; integer fvl_ut_cnt = 0, fvl_ut_bit = 0; reg [7:0] fvl_ut_sh = 8'd0;
  always @(posedge fvl_clk) if (fvl_preg[0] > 1) begin
    if (!fvl_ut_busy) begin
      if (TXBIT === 1'b0) begin fvl_ut_busy <= 1'b1; fvl_ut_cnt <= (fvl_preg[0] * 3) / 2 - 2; fvl_ut_bit <= 0; end
    end else if (fvl_ut_cnt > 0) fvl_ut_cnt <= fvl_ut_cnt - 1;
    else if (fvl_ut_bit < 8) begin
      fvl_ut_sh <= {TXBIT === 1'b1, fvl_ut_sh[7:1]}; fvl_ut_bit <= fvl_ut_bit + 1; fvl_ut_cnt <= fvl_preg[0] - 1;
    end else begin
      fvl_ut_busy <= 1'b0;
      if (TXBIT === 1'b1) $fwrite(32'h8000_0001, "@@FVL U %02h\n", fvl_ut_sh);
      else $fwrite(32'h8000_0001, "@@FVL u %02h\n", fvl_ut_sh);
    end
  end
"""

UART_DRIVER = r"""
  // ---- USB-UART: send queued bytes to the design (8N1, idle high) ----
  reg [7:0] fvl_urx_q [0:1023]; integer fvl_urx_h = 0, fvl_urx_t = 0;
  reg fvl_urx_busy = 1'b0; integer fvl_urx_cnt = 0, fvl_urx_bit = 0; reg [9:0] fvl_urx_sh = 10'h3FF;
  wire fvl_urx_line = fvl_urx_busy ? fvl_urx_sh[0] : 1'b1;
  always @(fvl_preg_ev) if (fvl_preg_last == 1 && fvl_urx_t - fvl_urx_h < 1024) begin
    fvl_urx_q[fvl_urx_t % 1024] = fvl_preg[1][7:0]; fvl_urx_t = fvl_urx_t + 1;
  end
  always @(posedge fvl_clk) if (fvl_preg[0] > 1) begin
    if (!fvl_urx_busy) begin
      if (fvl_urx_h != fvl_urx_t) begin
        fvl_urx_sh <= {1'b1, fvl_urx_q[fvl_urx_h % 1024], 1'b0}; fvl_urx_h <= fvl_urx_h + 1;
        fvl_urx_busy <= 1'b1; fvl_urx_cnt <= fvl_preg[0] - 1; fvl_urx_bit <= 0;
      end
    end else if (fvl_urx_cnt > 0) fvl_urx_cnt <= fvl_urx_cnt - 1;
    else begin
      fvl_urx_sh <= {1'b1, fvl_urx_sh[9:1]}; fvl_urx_cnt <= fvl_preg[0] - 1;
      if (fvl_urx_bit == 9) fvl_urx_busy <= 1'b0;
      fvl_urx_bit <= fvl_urx_bit + 1;
    end
  end
"""

VGA_MONITOR = r"""
  // ---- VGA monitor: learns sync polarity and pixel rate, reports 640x480 pixels (run-length) ----
  wire fvl_vg_hs = HSBIT, fvl_vg_vs = VSBIT;
  wire [11:0] fvl_vg_rgb = {RBITS, GBITS, BBITS};
  reg fvl_vg_hs_d = 1'b1, fvl_vg_vs_d = 1'b1;
  integer fvl_vg_hcnt = 0, fvl_vg_hper = 0, fvl_vg_hrun = 0, fvl_vg_hrun0 = 0, fvl_vg_hrun1 = 0;
  integer fvl_vg_line = 0, fvl_vg_vrun = 0, fvl_vg_vrun0 = 0, fvl_vg_vrun1 = 0;
  integer fvl_vg_nl = 0, fvl_vg_div = 4,fvl_vg_x, fvl_vg_y, fvl_vg_lx = -1, fvl_vg_ly = -1;
  reg [11:0] fvl_vg_last = 12'h000;
  reg fvl_vg_hact, fvl_vg_vact, fvl_vg_synced = 1'b0;
  always @(posedge fvl_clk) begin
    // the shorter of the two levels is the sync pulse (default: negative sync)
    fvl_vg_hact = (fvl_vg_hrun0 && fvl_vg_hrun1) ? (fvl_vg_hrun1 < fvl_vg_hrun0) : 1'b0;
    fvl_vg_vact = (fvl_vg_vrun0 && fvl_vg_vrun1) ? (fvl_vg_vrun1 < fvl_vg_vrun0) : 1'b0;
    fvl_vg_hcnt = fvl_vg_hcnt + 1; fvl_vg_hrun = fvl_vg_hrun + 1;
    if (fvl_vg_hs !== fvl_vg_hs_d && (fvl_vg_hs === 1'b0 || fvl_vg_hs === 1'b1)) begin
      if (fvl_vg_hs_d) fvl_vg_hrun1 = fvl_vg_hrun; else fvl_vg_hrun0 = fvl_vg_hrun;
      fvl_vg_hrun = 0; fvl_vg_hs_d = fvl_vg_hs;
      if (fvl_vg_hs == fvl_vg_hact && fvl_vg_hcnt < 64)          // power-on glitch, not a line
        fvl_vg_hcnt = 0;
      else if (fvl_vg_hs == fvl_vg_hact) begin                    // start of a line (hsync pulse)
        fvl_vg_hper = fvl_vg_hcnt; fvl_vg_hcnt = 0;
        fvl_vg_div = (fvl_vg_hper + 400) / 800; if (fvl_vg_div < 1) fvl_vg_div = 1;
        fvl_vg_line = fvl_vg_line + 1; fvl_vg_vrun = fvl_vg_vrun + 1;
        if (fvl_vg_nl < 2) fvl_vg_nl = fvl_vg_nl + 1;             // 2 = line period measured
      end
    end
    if (fvl_vg_vs !== fvl_vg_vs_d && (fvl_vg_vs === 1'b0 || fvl_vg_vs === 1'b1)) begin
      if (fvl_vg_vs_d) fvl_vg_vrun1 = fvl_vg_vrun; else fvl_vg_vrun0 = fvl_vg_vrun;
      fvl_vg_vrun = 0; fvl_vg_vs_d = fvl_vg_vs;
      if (fvl_vg_vs == fvl_vg_vact) begin                         // start of a frame (vsync pulse)
        if (fvl_vg_hper > 100 && fvl_vg_line > 10) begin         // ignore power-on, before sync is seen
          $fwrite(32'h8000_0001, "@@FVL F %0d %0d %0d\n", fvl_vg_line, fvl_vg_hper, fvl_vg_div);
          fvl_vg_synced = 1'b1;
        end
        fvl_vg_line = 0; fvl_vg_ly = -1;
      end
    end
    if (fvl_vg_nl >= 2 && (fvl_vg_hcnt % fvl_vg_div) == (fvl_vg_div / 2)) begin
      fvl_vg_x = fvl_vg_hcnt / fvl_vg_div - 144;                  // sync 96 + back porch 48
      // after a vsync: sync 2 + back porch 33 lines. Before the first one, assume the design
      // started at the top of the picture (counters reset to 0), so the image appears at once.
      fvl_vg_y = fvl_vg_synced ? fvl_vg_line - 35 : fvl_vg_line;
      if (fvl_vg_x >= 0 && fvl_vg_x < 640 && fvl_vg_y >= 0 && fvl_vg_y < 480 &&
          (fvl_vg_y != fvl_vg_ly || fvl_vg_rgb !== fvl_vg_last)) begin
        $fwrite(32'h8000_0001, "@@FVL P %0d %0d %h\n", fvl_vg_x, fvl_vg_y, fvl_vg_rgb);
        fvl_vg_last = fvl_vg_rgb; fvl_vg_ly = fvl_vg_y;
      end
    end
  end
"""

ADT7420 = r"""
  // ---- ADT7420 temperature sensor, I2C slave at 0x4B (registers 0x00-0x03, 0x0B ID) ----
  wire fvl_tmp_scl = SCLBIT, fvl_tmp_sda = SDABIT;
  reg fvl_tmp_low = 1'b0, fvl_tmp_act = 1'b0, fvl_tmp_ackph = 1'b0, fvl_tmp_rw = 1'b0, fvl_tmp_me = 1'b0;
  reg fvl_tmp_nack = 1'b0;
  reg [7:0] fvl_tmp_sh = 8'd0, fvl_tmp_ptr = 8'd0, fvl_tmp_out = 8'd0, fvl_tmp_cfg = 8'd0;
  integer fvl_tmp_bit = 0, fvl_tmp_byte = 0;
  function [7:0] fvl_tmp_reg(input [7:0] a);
    case (a)
      8'h00: fvl_tmp_reg = fvl_preg[2][15:8];
      8'h01: fvl_tmp_reg = fvl_preg[2][7:0];
      8'h02: fvl_tmp_reg = 8'h00;
      8'h03: fvl_tmp_reg = fvl_tmp_cfg;
      8'h0B: fvl_tmp_reg = 8'hCB;
      default: fvl_tmp_reg = 8'h00;
    endcase
  endfunction
  always @(negedge fvl_tmp_sda) if (fvl_tmp_scl === 1'b1) begin            // START / repeated START
    fvl_tmp_act = 1'b1; fvl_tmp_bit = 0; fvl_tmp_byte = 0; fvl_tmp_ackph = 1'b0; fvl_tmp_low = 1'b0; fvl_tmp_nack = 1'b0;
  end
  always @(posedge fvl_tmp_sda) if (fvl_tmp_scl === 1'b1) begin fvl_tmp_act = 1'b0; fvl_tmp_low = 1'b0; end   // STOP
  always @(posedge fvl_tmp_scl) if (fvl_tmp_act) begin
    if (!fvl_tmp_ackph) begin fvl_tmp_sh = {fvl_tmp_sh[6:0], fvl_tmp_sda === 1'b1}; fvl_tmp_bit = fvl_tmp_bit + 1; end
    else if (fvl_tmp_rw && fvl_tmp_byte > 0) fvl_tmp_nack = (fvl_tmp_sda === 1'b1);
  end
  always @(negedge fvl_tmp_scl) if (fvl_tmp_act) begin
    if (!fvl_tmp_ackph && fvl_tmp_bit == 8) begin                            // byte done: ACK phase
      fvl_tmp_ackph = 1'b1;
      if (fvl_tmp_byte == 0) begin
        fvl_tmp_me = (fvl_tmp_sh[7:1] == 7'h4B); fvl_tmp_rw = fvl_tmp_sh[0]; fvl_tmp_low = fvl_tmp_me;
      end else if (!fvl_tmp_rw) begin
        if (fvl_tmp_byte == 1) fvl_tmp_ptr = fvl_tmp_sh;
        else begin if (fvl_tmp_ptr == 8'h03) fvl_tmp_cfg = fvl_tmp_sh; fvl_tmp_ptr = fvl_tmp_ptr + 1; end
        fvl_tmp_low = fvl_tmp_me;
      end else fvl_tmp_low = 1'b0;                                            // master ACKs a read byte
    end else if (fvl_tmp_ackph) begin                                         // ACK phase over
      fvl_tmp_ackph = 1'b0; fvl_tmp_bit = 0; fvl_tmp_byte = fvl_tmp_byte + 1;
      if (fvl_tmp_rw && fvl_tmp_me && !fvl_tmp_nack) begin
        fvl_tmp_out = fvl_tmp_reg(fvl_tmp_ptr); fvl_tmp_ptr = fvl_tmp_ptr + 1; fvl_tmp_low = ~fvl_tmp_out[7];
      end else fvl_tmp_low = 1'b0;
    end else if (fvl_tmp_rw && fvl_tmp_me && fvl_tmp_byte > 0 && !fvl_tmp_nack)
      fvl_tmp_low = ~fvl_tmp_out[7 - fvl_tmp_bit];
  end
"""

ADXL362 = r"""
  // ---- ADXL362 accelerometer, SPI mode 0 (0x0A write, 0x0B read; data only in measurement mode) ----
  wire fvl_acl_sclk = SCLKBIT, fvl_acl_mosi = MOSIBIT, fvl_acl_csn = CSNBIT;
  reg fvl_acl_miso = 1'b0; reg [7:0] fvl_acl_sh = 0, fvl_acl_cmd = 0, fvl_acl_addr = 0, fvl_acl_pwr = 0;
  integer fvl_acl_bit = 0;
  function [7:0] fvl_acl_reg(input [7:0] a);
    reg signed [15:0] x, y, z; reg meas;
    begin
      meas = (fvl_acl_pwr[1:0] == 2'b10);
      x = meas ? fvl_preg[3][15:0] : 16'd0; y = meas ? fvl_preg[4][15:0] : 16'd0; z = meas ? fvl_preg[5][15:0] : 16'd0;
      case (a)
        8'h00: fvl_acl_reg = 8'hAD;  8'h01: fvl_acl_reg = 8'h1D;  8'h02: fvl_acl_reg = 8'hF2;  8'h03: fvl_acl_reg = 8'h01;
        8'h08: fvl_acl_reg = x[11:4]; 8'h09: fvl_acl_reg = y[11:4]; 8'h0A: fvl_acl_reg = z[11:4];
        8'h0B: fvl_acl_reg = {7'd0, meas};
        8'h0E: fvl_acl_reg = x[7:0];  8'h0F: fvl_acl_reg = {{4{x[15]}}, x[11:8]};
        8'h10: fvl_acl_reg = y[7:0];  8'h11: fvl_acl_reg = {{4{y[15]}}, y[11:8]};
        8'h12: fvl_acl_reg = z[7:0];  8'h13: fvl_acl_reg = {{4{z[15]}}, z[11:8]};
        8'h2D: fvl_acl_reg = fvl_acl_pwr;
        default: fvl_acl_reg = 8'h00;
      endcase
    end
  endfunction
  always @(fvl_acl_csn) begin fvl_acl_bit = 0; fvl_acl_miso = 1'b0; end
  always @(posedge fvl_acl_sclk) if (fvl_acl_csn === 1'b0) begin
    fvl_acl_sh = {fvl_acl_sh[6:0], fvl_acl_mosi === 1'b1}; fvl_acl_bit = fvl_acl_bit + 1;
    if (fvl_acl_bit == 8) fvl_acl_cmd = fvl_acl_sh;
    else if (fvl_acl_bit == 16) fvl_acl_addr = fvl_acl_sh;
    else if (fvl_acl_bit > 16 && fvl_acl_bit % 8 == 0 && fvl_acl_cmd == 8'h0A) begin
      if (fvl_acl_addr + (fvl_acl_bit - 24) / 8 == 8'h2D) fvl_acl_pwr = fvl_acl_sh;
    end
  end
  always @(negedge fvl_acl_sclk) if (fvl_acl_csn === 1'b0 && fvl_acl_cmd == 8'h0B && fvl_acl_bit >= 16)
    fvl_acl_miso = fvl_acl_reg(fvl_acl_addr + (fvl_acl_bit - 16) / 8) >> (7 - (fvl_acl_bit - 16) % 8);
"""

MIC = r"""
  // ---- PDM microphone: first-order sigma-delta of a sine tone (data changes on M_CLK falling edge) ----
  wire fvl_mic_clk = MCLKBIT;
  reg fvl_mic_data = 1'b0; real fvl_mic_acc = 0.0, fvl_mic_s;
  always @(negedge fvl_mic_clk) begin
    fvl_mic_s = (fvl_preg[6] / 1000.0) * $sin(6.283185307179586 * fvl_preg[7] * $realtime * 1.0e-9);
    if (fvl_mic_acc >= 0.0) begin fvl_mic_data = 1'b1; fvl_mic_acc = fvl_mic_acc + fvl_mic_s - 1.0; end
    else begin fvl_mic_data = 1'b0; fvl_mic_acc = fvl_mic_acc + fvl_mic_s + 1.0; end
  end
"""
