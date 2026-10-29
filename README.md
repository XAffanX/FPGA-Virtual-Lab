# FPGA Virtual Lab 1.2

Virtual Digilent boards that work alongside **AMD Vivado**. You write, implement and generate the
bitstream in Vivado; FPGA Virtual Lab provides a board to program it into.

| Virtual board | Real board | FPGA | Simulated parts |
|---|---|---|---|
| `virtual_basys3` | Digilent Basys 3 | XC7A35T-1CPG236C | 16 switches, 16 LEDs, 5 buttons, 4-digit display, USB-UART, VGA, Pmods JA/JB/JC/JXADC |
| `virtual_nexys4ddr` | Digilent Nexys 4 DDR | XC7A100T-1CSG324C | 16 switches, 16 LEDs, 2 tri-colour LEDs, 5 buttons + CPU RESET (active low), 8-digit display, USB-UART, VGA, Pmods JA–JD/JXADC, temperature sensor, accelerometer, microphone |

Both boards also have a 100 MHz clock, a power switch and LED, a PROG button and a DONE LED.
Pins come from Digilent's master XDC files; the Nexys file is included under `configurations/xdc/`.
Pins of peripherals that aren't simulated (DDR2, Ethernet, audio out, microSD, PS/2, XADC analog
inputs) are recognised and reported as not simulated.

## Workflow

1. In Vivado: write the design, apply the board's XDC, run implementation, and generate the
   bitstream.
2. In FPGA Virtual Lab: choose the **Board** (`virtual_basys3` or `virtual_nexys4ddr`), then click
   **⏻ Power ON**.
3. Program the board in one of two ways:
   * **Load Bitstream**, then **Program Virtual FPGA**. A `.bit` built for the other board is
     detected, and the app offers to switch boards.
   * **Connect to Vivado**. The app makes the board available to Vivado's Hardware Manager and
     shows Tcl to paste into Vivado's Tcl Console. If a bitstream is loaded, the Tcl programs it
     straight away. Click **Copy Tcl** and paste it into Vivado.
4. Flip switches and press buttons, and watch the LEDs and display. Use Run/Pause/Step, Reset, and
   **Power OFF** when you're done.

## Board devices (Devices page)

Each device is simulated only when your design uses its pins. The pins come from Digilent's master XDC.

| Device | What you can do |
|---|---|
| **USB-UART terminal** | See what the design sends, and type text to send it, at any baud rate (8N1). Includes hex view and line-ending choice. Basys 3: `RsRx`/`RsTx`; Nexys: `UART_TXD_IN`/`UART_RXD_OUT`. |
| **VGA screen** | 640×480 picture from the 12 colour pins and the sync signals. Sync polarity and pixel rate are detected like a monitor. The picture builds up line by line at simulation speed. |
| **Pmod headers** | Every Pmod pin (JA–JD, JXADC) is a board pin: outputs light up, and inputs are buttons you toggle. |
| **Nexys sensors** | ADT7420 temperature (I2C, slider), ADXL362 accelerometer (SPI, X/Y/Z or presets, data only in measurement mode like the real part), PDM microphone (a test tone with level and frequency). |

A ready-made VGA test (colour bars and a square moved by the switches) is in
`bitstreams/vga_test_basys3/`: load `vga_test.bit` on `virtual_basys3`.

## Timing simulation

Turn on **Simulation ▸ Timing simulation (Vivado's real delays)** and reprogram the board.

1. Vivado's timing netlist and SDF are exported along with the normal netlist (one export, cached).
2. The app writes every cell and wire delay (slow corner) into the netlist and simulates that.

While running continuously, a path longer than the 10 ns clock captures stale data, so a design
that fails timing misbehaves as it would on the board. The status panel shows Vivado's worst
slack (WNS) and says *FAILS* when it is negative. When you pause or single-step, the clock stops
and outputs settle, so a timing failure disappears, as with a slow manual clock on real hardware.

## How a `.bit` is run

The configuration frames inside a `.bit` aren't publicly documented, so they aren't decoded.
Instead:

1. The `<top>_routed.dcp` saved next to the `.bit` is located.
2. Vivado, in batch mode, exports that checkpoint as a netlist of 7-series primitives. This takes
   about 25 s the first time and is cached after that.
3. The netlist is simulated with Xilinx's UNISIM models in Icarus Verilog, with a 100 MHz clock in
   simulated time.

Pins come from Vivado's placement. "PROGRAMMED" is shown only after the simulator has started the
design.

## Connect to Vivado

Clicking **Connect to Vivado** starts the following and opens a dialog with the Tcl:
* **A virtual cable (XVC).** It emulates the JTAG port of the selected FPGA: IDCODE `0x0362D093`
  for the XC7A35T, or `0x03631093` for the XC7A100T, plus the configuration logic.
* **A virtual host.** This is AMD's own `hw_server` on `127.0.0.1:3122`, with the cable already
  attached.

The Tcl leaves Vivado's local server (Vivado holds only one server connection at a time),
connects to the virtual host, and opens the device: `xc7a35t_0` or `xc7a100t_0`.

**If a real USB JTAG board is plugged in**, the virtual host isn't started: a second `hw_server`
could take the real board's USB cable. The Tcl then adds the virtual cable to Vivado's own local
server instead, so the real and virtual boards appear as two targets. The virtual host also stops
by itself if you plug a board in while it's running.

**Programming from Vivado:**
* The received configuration is matched byte for byte against the loaded bitstream and the files
  Vivado records (`PROGRAM.FILE` history, recent projects).
* An unknown or wrong-board configuration leaves DONE low, so Vivado reports *End of startup
  status: LOW* and the app's console says why.

Tested with Vivado 2026.1 in Tcl mode for both boards, in both modes.

**Board names in Vivado:** inside the app the boards are called `virtual_basys3` and
`virtual_nexys4ddr`, and the Tcl prints `Connected to virtual_…`. Vivado's own target and device
names (`127.0.0.1:3122/…`, `xc7a35t_0`) come from the server address and the FPGA. A custom name
there would need an administrator-only hosts-file entry, so the app doesn't change it.

## Power, PROG and Reset

| Control | Effect |
|---|---|
| Power ON / OFF (toolbar, or the switch on the board) | OFF stops the clock and ends the simulator, turns all outputs off and loses the configuration. ON leaves the FPGA unprogrammed. |
| PROG button (on the board) | Clears the configuration (DONE goes low). |
| Reset ▸ Restart simulation | Design back to its initial state, cycle 0. |
| Reset ▸ Hardware reset | Holds a design input (e.g. `btnC`, `rst_n`, `CPU_RESETN`) at its active level for N cycles. Polarity is suggested from the name. |
| CPU RESET (Nexys) | Active-low button: the FPGA pin reads 1 until you press it. Shortcut: Esc. |

## Run from source / tests / build

```powershell
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python main.py
.venv\Scripts\python -m pytest                                   # 101 tests, needs Vivado for most
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1  # dist\FPGAVirtualLab\ + zip
```

The test fixtures in `tests/fixtures` are real Vivado 2026.1 bitstreams with their routed
checkpoints: six for the Basys 3 (counters, UART echo, VGA, Pmod, a design that fails timing)
and two for the Nexys 4 DDR (counter, on-board sensors). Their sources are in `tests/fixtures/designs`. The tests that use Vivado start
their own private `hw_server`, so they never touch an open Vivado session.

## Layout

```
app/hardware/   VirtualBoard state machine (power, programming, reset, board switching, Vivado link)
app/board/      board definitions + geometry (Basys 3, Nexys 4 DDR)
app/vivado/     .bit header/validation, checkpoint export, discovery of Vivado's bitstreams
app/xvc/        XVC server, emulated JTAG/configuration logic, virtual host, USB-cable detection
app/simulation/ simulation engine (harness, vvp session, worker thread)
app/ui/, app/widgets/   main window, status panel, Connect dialog, board view, waveforms, console
configurations/ basys3.json, nexys4ddr.json (+ Digilent master XDC)
scripts/        gen_nexys4ddr_json.py, gen_board_svg.py, build_windows.ps1
```
