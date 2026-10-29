"""Emulated JTAG port of a Xilinx XC7A35T (Artix-7) for Xilinx Virtual Cable (XVC).

What is modelled (UG470 "7 Series FPGAs Configuration", IEEE 1149.1):
  * the 16-state TAP controller, 6-bit instruction register with the 7-series
    IR capture value {DONE, INIT_COMPLETE, ISC_ENABLED, ISC_DONE, 0, 1}
  * IDCODE (0x0362D093), USERCODE, BYPASS, FUSE_DNA, XADC_DRP and USER1-4 data registers
  * JPROGRAM / CFG_IN / CFG_OUT / JSTART / JSHUTDOWN: a configuration packet processor that
    synchronises on 0xAA995566, decodes type-1/type-2 packets, checks the IDCODE written by the
    bitstream, executes CMD register commands and answers register reads (STAT, IDCODE, ...)
    through CFG_OUT.

What is NOT modelled: the configuration frames are captured but not decoded (the frame format is
not public), and the bitstream CRC is not recomputed. Whether the device reaches DONE is decided by
the `resolver` callback, which identifies the received configuration (FPGA Virtual Lab matches it
byte-for-byte against known .bit files and runs that design's implemented netlist). An unknown
configuration therefore honestly leaves DONE low.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

IDCODE_XC7A35T = 0x0362D093
IR_LEN = 6

# 7-series JTAG instructions (UG470 table 6-3)
IR_EXTEST, IR_USER1, IR_USER2, IR_CFG_OUT, IR_CFG_IN = 0x26, 0x02, 0x03, 0x04, 0x05
IR_USERCODE, IR_IDCODE, IR_JPROGRAM, IR_JSTART, IR_JSHUTDOWN = 0x08, 0x09, 0x0B, 0x0C, 0x0D
IR_USER3, IR_USER4, IR_FUSE_DNA, IR_XADC_DRP, IR_BYPASS = 0x22, 0x23, 0x32, 0x37, 0x3F

TLR, RTI, SEL_DR, CAP_DR, SH_DR, EX1_DR, P_DR, EX2_DR, UP_DR, SEL_IR, CAP_IR, SH_IR, EX1_IR, P_IR, EX2_IR, UP_IR = \
    range(16)
NEXT = {  # state: (next if TMS=0, next if TMS=1)
    TLR: (RTI, TLR), RTI: (RTI, SEL_DR), SEL_DR: (CAP_DR, SEL_IR), CAP_DR: (SH_DR, EX1_DR),
    SH_DR: (SH_DR, EX1_DR), EX1_DR: (P_DR, UP_DR), P_DR: (P_DR, EX2_DR), EX2_DR: (SH_DR, UP_DR),
    UP_DR: (RTI, SEL_DR), SEL_IR: (CAP_IR, TLR), CAP_IR: (SH_IR, EX1_IR), SH_IR: (SH_IR, EX1_IR),
    EX1_IR: (P_IR, UP_IR), P_IR: (P_IR, EX2_IR), EX2_IR: (SH_IR, UP_IR), UP_IR: (RTI, SEL_DR),
}
STATE_NAMES = "TLR RTI SEL_DR CAP_DR SH_DR EX1_DR P_DR EX2_DR UP_DR SEL_IR CAP_IR SH_IR EX1_IR P_IR EX2_IR UP_IR".split()

# configuration registers (UG470 table 5-23) and commands (table 5-25)
R_CRC, R_FAR, R_FDRI, R_FDRO, R_CMD, R_CTL0, R_MASK, R_STAT, R_LOUT, R_COR0, R_MFWR, R_CBC, R_IDCODE, \
    R_AXSS, R_COR1, R_WBSTAR, R_TIMER, R_BOOTSTS, R_CTL1, R_BSPI = \
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 22, 24, 31
CMD_START, CMD_RCRC, CMD_DESYNC, CMD_SHUTDOWN = 5, 7, 13, 11
SYNC_WORD = 0xAA995566

_REV = bytes(int(f"{b:08b}"[::-1], 2) for b in range(256))


def bits_to_bytes_msb_first(value: int, nbits: int) -> bytes:
    """Bits shifted in order (first bit = LSB of `value`) packed MSB-first into bytes."""
    nbytes = (nbits + 7) // 8
    return value.to_bytes(nbytes, "little").translate(_REV)


@dataclass
class ConfigResult:
    ok: bool
    message: str
    data: bytes = b""


@dataclass
class Artix7Jtag:
    """One XC7A35T on the JTAG chain."""
    resolver: object = None            # callable(bytes) -> (bool, str)
    on_jprogram: object = None         # callable() when configuration is cleared
    on_configured: object = None       # callable(ConfigResult) after JSTART
    powered: bool = True
    dna: int = 0x0123_4567_89AB_CDE0 & ((1 << 57) - 1)   # virtual device DNA
    idcode: int = IDCODE_XC7A35T
    log: list = field(default_factory=list)

    def __post_init__(self):
        self.lock = threading.RLock()
        self.state = TLR
        self.ir = IR_IDCODE
        self.sr = 0                    # active shift register
        self.sr_len = 1
        self.done = False
        self.init_complete = True
        self.id_error = False
        self.crc_error = False
        self.startup_requested = False
        self.config_chunks: list[tuple[int, int]] = []   # bits shifted into CFG_IN this DR scan
        self.session = bytearray()      # all configuration bytes since JPROGRAM
        self.synced = False
        self.out_fifo: list[int] = []   # readback words for CFG_OUT
        self.regs: dict[int, int] = {}
        self.xadc_result = 0
        self.last_result: ConfigResult | None = None
        self.instr_counts: dict[int, int] = {}

    # ------------------------------------------------------------------ status values
    def ir_capture(self) -> int:
        # bit2 = ISC_DONE: set once a configuration has completed (configured parts read 0b110101)
        return (int(self.done) << 5) | (int(self.init_complete) << 4) | (int(self.done) << 2) | 0b000001

    def stat(self) -> int:
        v = 0
        v |= int(self.crc_error) << 0
        v |= 1 << 3                         # DCI_MATCH
        v |= int(self.done) << 2            # MMCM_LOCK (no MMCMs waiting)
        v |= int(self.done) << 4            # EOS (end of start-up)
        v |= int(self.done) << 5            # GTS_CFG_B
        v |= int(self.done) << 6            # GWE
        v |= int(self.done) << 7            # GHIGH_B
        v |= 0b001 << 8                     # MODE pins: master SPI (Basys 3 default)
        v |= int(self.init_complete) << 11  # INIT_COMPLETE
        v |= 1 << 12                        # INIT_B
        v |= int(self.done) << 13           # RELEASE_DONE
        v |= int(self.done) << 14           # DONE
        v |= int(self.id_error) << 15       # ID_ERROR
        v |= (0b100 if self.done else 0) << 18   # STARTUP_STATE
        return v

    # ------------------------------------------------------------------ DR selection
    def _capture_dr(self):
        ir = self.ir
        if ir == IR_IDCODE:
            self.sr, self.sr_len = self.idcode, 32
        elif ir == IR_USERCODE:
            self.sr, self.sr_len = 0xFFFFFFFF, 32
        elif ir == IR_FUSE_DNA:
            self.sr, self.sr_len = self.dna, 64
        elif ir == IR_XADC_DRP:
            self.sr, self.sr_len = self.xadc_result, 32
        elif ir == IR_CFG_OUT:
            bits, n = 0, 0
            for w in self.out_fifo:            # each word MSB first
                for k in range(31, -1, -1):
                    bits |= ((w >> k) & 1) << n
                    n += 1
            self.out_fifo.clear()
            self.sr, self.sr_len = bits, max(n, 1)
        elif ir == IR_CFG_IN:
            self.config_chunks = []
            self.sr, self.sr_len = 0, 1
        elif ir in (IR_USER1, IR_USER2, IR_USER3, IR_USER4):
            self.sr, self.sr_len = 0, 1        # no BSCANE2 in the design: 1-bit, reads 0 (no debug hub)
        else:
            self.sr, self.sr_len = 0, 1        # BYPASS and unimplemented instructions

    def _update_dr(self, value: int, nbits: int):
        if self.ir == IR_CFG_IN:
            self._process_cfg_in()
        elif self.ir == IR_XADC_DRP:
            self._xadc(value)

    def _update_ir(self, value: int):
        ir = value & 0x3F
        self.ir = ir
        self.instr_counts[ir] = self.instr_counts.get(ir, 0) + 1
        if ir == IR_JPROGRAM:
            self.done = False
            self.synced = False
            self.session = bytearray()
            self.regs.clear()
            self.out_fifo.clear()
            self.id_error = self.crc_error = False
            self.startup_requested = False
            self.init_complete = True
            self.last_result = None
            if self.on_jprogram:
                self.on_jprogram()
        elif ir == IR_JSTART:
            self._startup()
        elif ir == IR_JSHUTDOWN:
            pass

    # ------------------------------------------------------------------ configuration logic
    def _process_cfg_in(self):
        if not self.config_chunks:
            return
        value, n = 0, 0
        for v, k in self.config_chunks:
            value |= v << n
            n += k
        self.config_chunks = []
        data = bits_to_bytes_msb_first(value, n)[: n // 8]
        self.session += data
        self._packets(data)

    def _packets(self, data: bytes):
        words = [int.from_bytes(data[i:i + 4], "big") for i in range(0, len(data) - 3, 4)]
        i = 0
        reg = None
        if not self.synced:
            # The sync word may not be 32-bit aligned relative to the scan start.
            idx = data.find(SYNC_WORD.to_bytes(4, "big"))
            if idx < 0:
                return
            self.synced = True
            data = data[idx + 4:]
            words = [int.from_bytes(data[i:i + 4], "big") for i in range(0, len(data) - 3, 4)]
        while i < len(words):
            h = words[i]
            i += 1
            typ = h >> 29
            op = (h >> 27) & 3
            if typ == 1:
                reg = (h >> 13) & 0x1F
                count = h & 0x7FF
            elif typ == 2 and reg is not None:
                count = h & 0x7FFFFFF
            else:
                continue          # filler / dummy words
            if op == 2:            # write
                payload = words[i:i + count]
                i += count
                self._write_reg(reg, payload)
                if not self.synced:
                    return
            elif op == 1:          # read
                self._read_reg(reg, count)

    def _write_reg(self, reg: int, payload: list[int]):
        if not payload:
            return
        if reg == R_FDRI:
            return                 # frame data: kept in self.session, not decoded
        last = payload[-1]
        self.regs[reg] = last
        if reg == R_IDCODE and last != self.idcode:
            self.id_error = True
        elif reg == R_CMD:
            if last == CMD_START:
                self.startup_requested = True
            elif last == CMD_DESYNC:
                self.synced = False
            elif last == CMD_RCRC:
                self.crc_error = False

    def _read_reg(self, reg: int, count: int):
        if reg == R_STAT:
            words = [self.stat()]
        elif reg == R_IDCODE:
            words = [self.idcode]
        elif reg == R_BOOTSTS:
            words = [0x00000001 if self.done else 0]
        else:
            words = [self.regs.get(reg, 0)]
        self.out_fifo += (words * count)[:max(count, 1)]

    def _startup(self):
        if self.done:
            return
        data = bytes(self.session)
        if not data:
            return
        if self.id_error:
            res = ConfigResult(False, f"bitstream IDCODE does not match this device (0x{self.idcode:08X})", data)
        elif not self.startup_requested:
            res = ConfigResult(False, "configuration data did not contain a START command", data)
        elif self.resolver is None:
            res = ConfigResult(False, "no bitstream resolver", data)
        else:
            ok, msg = self.resolver(data)
            res = ConfigResult(ok, msg, data)
        self.done = res.ok
        self.last_result = res
        if self.on_configured:
            self.on_configured(res)

    def _xadc(self, cmd: int):
        op = (cmd >> 26) & 0xF
        addr = (cmd >> 16) & 0x3FF
        if op == 1:   # read: plausible on-chip sensor values for a virtual device
            table = {0x00: 0x9C80,   # temperature ~35 C
                     0x01: 0x5550,   # VCCINT 1.00 V
                     0x02: 0x99A0,   # VCCAUX 1.80 V
                     0x06: 0x5550}   # VCCBRAM 1.00 V
            self.xadc_result = table.get(addr, 0)
        else:
            self.xadc_result = 0

    # ------------------------------------------------------------------ TCK processing
    def shift(self, nbits: int, tms: bytes, tdi: bytes) -> bytes:
        """Process one XVC shift: returns TDO bits (LSB first)."""
        with self.lock:
            if not self.powered:
                self.state = TLR
                return bytes(len(tms))
            tms_i = int.from_bytes(tms, "little")
            tdi_i = int.from_bytes(tdi, "little")
            tdo_i = 0
            i = 0
            while i < nbits:
                st = self.state
                # Fast paths: long CFG_IN data scans and Run-Test/Idle clocking.
                if (st == SH_DR and self.ir == IR_CFG_IN) or st == RTI:
                    m = tms_i >> i
                    j = nbits if m == 0 else i + ((m & -m).bit_length() - 1)
                    if j > i:
                        if st == SH_DR:
                            self.config_chunks.append(((tdi_i >> i) & ((1 << (j - i)) - 1), j - i))
                        i = j
                        continue
                tms_b = (tms_i >> i) & 1
                tdi_b = (tdi_i >> i) & 1
                if st == CAP_DR:
                    self._capture_dr()
                    self._shifted_in = 0
                    self._shift_count = 0
                elif st == CAP_IR:
                    self.sr, self.sr_len = self.ir_capture(), IR_LEN
                    self._shift_count = 0
                elif st in (SH_DR, SH_IR):
                    tdo_i |= (self.sr & 1) << i
                    if st == SH_DR and self.ir == IR_CFG_IN:
                        self.config_chunks.append((tdi_b, 1))
                    self.sr = (self.sr >> 1) | (tdi_b << (self.sr_len - 1))
                    self._shift_count += 1
                elif st == UP_DR:
                    self._update_dr(self.sr, self.sr_len)
                elif st == UP_IR:
                    self._update_ir(self.sr)
                self.state = NEXT[st][tms_b]
                if self.state == TLR and st != TLR:
                    self.ir = IR_IDCODE
                i += 1
            return tdo_i.to_bytes(len(tms), "little")
