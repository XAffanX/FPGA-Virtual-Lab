"""Bitstream checks before programming: Xilinx header, completeness and target FPGA."""
from __future__ import annotations

from pathlib import Path

from .bitfile import BitfileError, BitInfo, read_bit_header


class WrongBoardError(BitfileError):
    """The .bit is valid but built for another FPGA; `board` is the supported board it fits, if any."""

    def __init__(self, message: str, board=None):
        super().__init__(message)
        self.board = board


def validate_bitstream(path: Path, board) -> BitInfo:
    """Raise BitfileError (or WrongBoardError) with a user-readable reason, else return the header."""
    from app.board.board import board_for_part
    path = Path(path)
    if not path.is_file():
        raise BitfileError(f"file not found: {path}")
    if path.suffix.lower() != ".bit":
        raise BitfileError(f"{path.name} is not a .bit file")
    try:
        info = read_bit_header(path)
    except OSError as exc:
        raise BitfileError(f"cannot read {path.name}: {exc}") from exc
    size = path.stat().st_size
    if size < info.expected_size:
        raise BitfileError(f"{path.name} is incomplete ({size:,} of {info.expected_size:,} bytes) - "
                           "Vivado may still be writing it, or the file was truncated")
    if not info.full_part.lower().startswith(board.part):
        other = board_for_part(info.full_part)
        hint = (f" It is a {other.name} bitstream - switch the board to {other.virtual_name}." if other else
                " No supported virtual board uses that FPGA.")
        raise WrongBoardError(f"{path.name} targets {info.full_part}, but {board.virtual_name} has an "
                              f"{board.part.upper()}.{hint}", other)
    return info
