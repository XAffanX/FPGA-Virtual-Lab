# Architecture

## Simulation pipeline

```
 GUI thread                     worker QThread                     vvp process (Icarus)
 ──────────                     ──────────────                     ────────────────────
 BoardWidget  ── input ──▶  SimWorker ─▶ BoardSimulator ─▶ SimulationSession ── stdin ──▶ fvl_tb harness
 (switch/btn)                (run loop,     (BoardIO,        (protocol,         "op a hex"    │ $fscanf
                              speed,         persistence)     reader thread)                  ▼
 LEDs / 7-seg ◀─ update ───  throttle) ◀─────────────────── ◀── stdout ── "@@FVL S ..." ── user design
 Waveforms ◀──────────────────────────── waves.vcd ◀──────────────────── $dumpvars ──────────┘
```

1. **Compile** (`app/compiler/compiler.py`). `iverilog -s <top>` checks and elaborates the user
   design. The exact port list, with directions, widths and declared `[msb:lsb]` ranges, is read
   from the generated `.vvp`. It never comes from a regex parser. Diagnostics are turned into
   file/line records.
2. **Program**. `app/simulation/harness.py` generates `fvl_tb`, which instantiates the top module.
   Each input port is driven by a harness register, and clock-mapped bits are ORed with the
   harness clock. Each output port goes into one concatenated output vector. The harness and the
   design are compiled together, and one `vvp` process is started.
3. **Protocol**. Each command is one line, `op a b`. The harness reads it with
   `$fscanf(STDIN, "%d %d %h")` and answers with exactly one status line:
   `@@FVL S <cycles> <time_ps> <outputs hex> <overflow>`. A run can also emit
   `@@FVL E <time_ps> <outputs>` lines when outputs change. Any other stdout line was printed by
   the design (`$display`) and is shown in the console.
4. **Timing**.
   * The clock period is 10 ns.
   * Inputs change while the clock is low, and are followed by a 1 ps settle step before outputs
     are reported. That way, combinational paths and non-blocking assignments have finished.
   * The harness counts time spent in the low phase, so rising edges stay exactly 10 ns apart
     however many inputs change.
   * `run n` produces n rising and n falling edges. Outputs are read after the falling edge, plus
     the settle step.
5. **Power-on values**. Input values at time 0 are passed as `+fvl_i<k>=<hex>` plusargs, so an
   active-low reset tied high never sees a spurious negedge. The optional `$fvl_power_on_init`
   VPI task fills X/Z bits of registers, integers and memories with 0.
6. **Reset** restarts the `vvp` process with the compiled model and the current switch positions.
   This is deterministic. It can then pulse a configured reset port. This is the only time a new
   process is started.
7. **Display**. `persistence.py` keeps timestamped output intervals and computes, per segment and
   per LED, the fraction of time it was lit. The window is the shorter of:
   * 20 ms of *simulated* time (human-eye persistence on real hardware), and
   * the simulated time covered during the last 0.25 s of *running*.

   A fast multiplexed display therefore looks steady, and slow clocks still update promptly. While
   paused, LEDs show their exact level.
8. **Speed**. The worker runs chunks every 30 ms, sized from the target cycles/s. In Maximum mode,
   chunks adapt to about 60 ms of wall time. Pause, input changes and Stop are handled between
   chunks. Stop can also kill the process during a chunk.

## Known limitations (V1)

* **Speed**. Icarus Verilog runs roughly 0.1–1 M cycles/s, depending on the design and whether
  waveform recording is on. A real board runs 100 M cycles/s. Designs that divide the clock by
  2^24 or more take seconds to minutes of wall time per visible change. Simulated time is always
  exact.
* **Event cap**. Output-change events used for persistence rendering are capped at 20 000 per
  chunk. Outputs that toggle faster than that, such as fast PWM, show exact end-of-chunk values
  but an approximate brightness.
* **Momentary buttons while paused** apply immediately, so clocked logic cannot see a click
  unless clock cycles pass. To work around this:
  * hold the key and Step, or
  * latch the button (right-click), or
  * press it while running. A press then lasts at least the configured minimum number of cycles.
* **Unsupported I/O**. DDR2, Ethernet, audio out, microSD, PS/2 and the XADC analog inputs are
  recognised in XDC files but not simulated. `inout` ports are connected to a pulled-up wire;
  only the I2C sensor drives one.
* **SystemVerilog**. `.sv` files use `iverilog -g2012`, which covers only part of SystemVerilog.
  Unsupported constructs are reported as compile errors.
* **`.bit` files** (`app/vivado/`). The configuration frames aren't decoded. The app simulates
  the routed checkpoint from the same Vivado run, exported as a netlist (see the README).
  Limitations:
  * It needs Vivado and the run folder.
  * Encrypted IP can't be exported.
  * Primitives whose UNISIM models Icarus can't compile (some MMCM/PLL, XADC and
    SystemVerilog-only models) will fail with a compile error.
  * The start-up global reset (GSR) is shortened to 1 ps so cycle 1 matches the RTL.
* **RTL with vendor primitives**. RTL that instantiates vendor primitives (MMCM, BRAM IP, etc.)
  only compiles if the user adds simulation models.
* **Waveform files**. Recording dumps the whole design while it is on, so files can grow quickly.
  Recording stops automatically at the size limit set in Settings (default 200 MB).
* **Two boards**. `virtual_basys3` and `virtual_nexys4ddr`. A new board needs:
  * a `configurations/<id>.json` (generated from Digilent's master XDC, like
    `scripts/gen_nexys4ddr_json.py`);
  * a layout in `app/board/layout.py`;
  * an entry in `scripts/gen_board_svg.py`.


## Version 1.1: virtual hardware layer

```
 Vivado (impl_1/*.bit + *_routed.dcp)          Vivado Hardware Manager
        |  Load Bitstream                              |  virtual host 127.0.0.1:3122 -> XVC localhost:2542
        v                                              v
 VirtualBoard state machine  <----- JPROGRAM / JSTART + byte-exact match ----- Artix7Jtag (TAP + config packets)
 (POWER OFF, UNPROGRAMMED, PROGRAMMING, PROGRAMMED, RUNNING, PAUSED)
        |  program: Vivado export (cached) -> netlist compile -> SimController.program
        v
 existing simulation engine (harness + persistent vvp, unchanged)
```

* **Power** is modelled as volatile configuration. Power OFF terminates the simulator process,
  clears the outputs and invalidates the configuration. Power ON leaves the FPGA unprogrammed.
  Every control checks the state, so contradictory actions are refused with a message (for
  example, Run while off, or Program while off).
* **Reset**:
  * *Restart simulation* (`BoardSimulator.restart`) restarts the vvp process from the same model,
    with the current switch positions applied from time 0.
  * *Hardware reset* (`pulse_reset`) drives a chosen 1-bit design input to its active level for N
    clock cycles, then restores the board value. Candidate inputs are the 1-bit non-clock inputs;
    common reset names come first, and `*_n`/`*n` names default to active-low.
* **Bitstream validation** (`app/vivado/validate.py`). A `.bit` is accepted only when:
  * its size equals header length plus the configuration length announced in the header, and
  * its part matches the selected board (`xc7a35tcpg236` or `xc7a100tcsg324`).

  A bitstream for the other supported board raises `WrongBoardError`, naming that board.
* **Boards** (`app/board/`). Each board is data: resources with pins, the FPGA part, the JTAG
  IDCODE, and a layout. Active-low inputs such as Nexys `CPU_RESETN` idle at logic 1 in `BoardIO`.
  Tri-colour LED channels are ordinary output resources, averaged over time like the LEDs.
  Switching boards powers the old one off, swaps the emulated JTAG IDCODE, and rebuilds the board
  view.
* **XVC**. Vivado's `hw_server` detects the emulated device, reads its IR/STAT/DNA/XADC, and
  programs it through JPROGRAM, CFG_IN and JSTART. The emulator synchronises on `0xAA995566`,
  decodes the packets, checks the IDCODE written by the bitstream, and requires a START command.
  At JSTART, DONE goes high only if the received data contains, byte for byte, the payload of a
  `.bit` the app knows and that has a routed checkpoint. That design is then programmed through
  the normal path.

### Hardware Manager limitations

* **Frame data isn't decoded and the CRC isn't recomputed.** An unknown configuration can't be
  simulated, so it is reported honestly as a failed start-up (DONE low).
* **Debug cores aren't supported.** The USER1-4 registers read as an empty BSCAN, so ILA/VIO
  aren't available.
* **No readback or verify.** Readback (`verify_hw_devices`), eFUSE and encryption aren't modelled.
  The XADC returns fixed sensor values, and the DNA is a fixed virtual value.
* **First open can race.** `hw_server` scans a new cable about every 2 s, so `open_hw_target
  -xvc_url` can report *No devices detected* the first time. Opening the target again works; the
  bundled Tcl snippet retries automatically.
* **What was tested.** Tested with Vivado 2026.1 in batch (Tcl) mode, for detection, programming,
  status and the unknown-bitstream case. The GUI dialogs drive the same `hw_server`/Tcl commands
  but were not exercised by an automated test.


## Board peripherals (`app/simulation/peripherals.py`)

Peripherals are Verilog models generated into the harness when the design maps their pins.
* The harness can override design input bits (UART RX, MISO, M_DATA), drive `inout` bits
  open-drain with a pull-up (I2C SDA), and read any output.
* Python configures the models through harness registers (op 4). The models report on stdout:
  * `U` — a UART byte;
  * `P x y rgb` — a VGA pixel run;
  * `F` — a VGA frame start.
* VGA pins are excluded from the LED event stream, so busy video doesn't crowd out LED and
  display persistence.

The VGA monitor assumes standard 640×480 timing (sync start, then 144 pixels to active video, and
2+33 lines). The pixel clock is derived from the measured hsync period / 800, and the sync
polarity is the shorter of the two levels.

## Timing simulation (`app/vivado/sdf.py`)

Icarus rejects Vivado's SDF, and the UNISIM `XIL_TIMING` models need timing-check delayed signals
that Icarus doesn't implement. So the annotator:
* removes `` `define XIL_TIMING ``;
* parses the SDF (max corner);
* replaces each cell pin connection with a delayed wire: inputs get interconnect + IOPATH delay;
  sequential outputs get clock-to-Q.

The harness then reads outputs just before the next rising edge (not 1 ps after the falling
edge). Inputs get 20 ns to propagate, and op 5 lets outputs settle when the clock stops.

### Limitations

* **Delays use the max corner only.** Hold violations are only approximated.
* **No setup/hold checks.** A late signal captures stale data rather than going metastable, and
  no violation warning is printed; Vivado's timing report gives the full analysis.
* **Clocked primitives without SDF IOPATHs** (e.g. some IP blocks) get only their wire delays.
