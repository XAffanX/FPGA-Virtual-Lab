"""Translate between board resources (switches, buttons, LEDs ...) and design port bits."""
from __future__ import annotations

from app.board.board import Board
from app.mapping.mapping import ResolvedMapping

from .harness import output_layout


class BoardIO:
    def __init__(self, board: Board, resolved: ResolvedMapping):
        self.board = board
        self.rm = resolved
        self.ports = resolved.ports
        # Logic level seen by the FPGA. Active-low inputs (e.g. CPU_RESETN) idle at 1.
        self.active_low = {r.id for r in board.inputs() if r.active_low}
        self.input_state: dict[str, int] = {r.id: (1 if r.id in self.active_low else 0)
                                            for r in board.inputs() if r.id != board.clock_resource}
        # Output resource -> absolute bit position inside the concatenated output vector.
        offsets = {l.port_index: l.offset for l in output_layout(self.ports)}
        self.output_bits: dict[str, int] = {rid: offsets[ref.port] + ref.pos
                                            for rid, ref in resolved.outputs.items()}

    def clock_masks(self) -> dict[int, int]:
        masks: dict[int, int] = {}
        for ref in self.rm.clock_bits:
            masks[ref.port] = masks.get(ref.port, 0) | (1 << ref.pos)
        return masks

    def port_values(self) -> dict[int, int]:
        """Value of every input port given the current board input state."""
        vals = {i: 0 for i in self.rm.input_ports()}
        for ref, v in self.rm.constants:
            if v:
                vals[ref.port] |= 1 << ref.pos
        for rid, refs in self.rm.inputs.items():
            if self.input_state.get(rid, 0):
                for ref in refs:
                    vals[ref.port] |= 1 << ref.pos
        return vals

    def ports_for(self, resource_id: str) -> set[int]:
        return {ref.port for ref in self.rm.inputs.get(resource_id, [])}

    def set_input(self, resource_id: str, active: int) -> set[int]:
        """A switch turned on / button pressed (active=1) or off/released (0); returns affected ports.

        For active-low inputs the FPGA pin goes low while the button is pressed."""
        if resource_id not in self.input_state:
            raise KeyError(f"{resource_id} is not a board input")
        level = 1 if active else 0
        if resource_id in self.active_low:
            level ^= 1
        self.input_state[resource_id] = level
        return self.ports_for(resource_id)

    def resource_level(self, resource_id: str, value: int, xmask: int) -> int | None:
        """Logic level of an output resource in a concatenated output vector (None = X/Z/unmapped)."""
        bit = self.output_bits.get(resource_id)
        if bit is None or (xmask >> bit) & 1:
            return None
        return (value >> bit) & 1
