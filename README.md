# FPGA Virtual Lab 1.1

Virtual Digilent boards that work alongside **AMD Vivado**. You write, implement and generate the
bitstream in Vivado; FPGA Virtual Lab provides a board to program it into.

| Virtual board | Real board | FPGA | Simulated parts |
|---|---|---|---|
| `virtual_basys3` | Digilent Basys 3 | XC7A35T-1CPG236C | 16 switches, 16 LEDs, 5 buttons, 4-digit display |
| `virtual_nexys4ddr` | Digilent Nexys 4 DDR | XC7A100T-1CSG324C | 16 switches, 16 LEDs, 2 tri-colour LEDs, 5 buttons + CPU RESET (active low), 8-digit display |

Both boards also have a 100 MHz clock, a power switch and LED, a PROG button and a DONE LED.
Pins come from Digilent's master XDC files; the Nexys file is included under `configurations/xdc/`.
Pins of peripherals that aren't simulated (Pmods, VGA, audio, sensors, DDR2, Ethernet) are
recognised and reported as not simulated.

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
.venv\Scripts\python -m pytest                                   # 84 tests, needs Vivado for most
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1  # dist\FPGAVirtualLab\ + zip
```

The test fixtures in `tests/fixtures` are real Vivado 2026.1 bitstreams with their routed
checkpoints: two for the Basys 3 and one for the Nexys 4 DDR. The tests that use Vivado start
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
