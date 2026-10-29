"""Bitstream validation against the selected virtual board."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.board.board import board_for_part, get_board
from app.vivado.bitfile import BitfileError
from app.vivado.validate import WrongBoardError, validate_bitstream

FIX = Path(__file__).parent / "fixtures"
BASYS = FIX / "vivado_counter" / "counter.bit"
NEXYS = FIX / "vivado_nexys4ddr" / "nexys_demo.bit"


def test_valid_for_matching_board():
    assert validate_bitstream(BASYS, get_board("basys3")).design == "counter"
    assert validate_bitstream(NEXYS, get_board("nexys4ddr")).full_part == "xc7a100tcsg324"


def test_wrong_board_names_the_right_one():
    with pytest.raises(WrongBoardError) as e:
        validate_bitstream(NEXYS, get_board("basys3"))
    assert e.value.board.id == "nexys4ddr" and "virtual_nexys4ddr" in str(e.value)
    with pytest.raises(WrongBoardError) as e:
        validate_bitstream(BASYS, get_board("nexys4ddr"))
    assert e.value.board.id == "basys3"


def test_unknown_part_missing_and_truncated(tmp_path):
    data = bytearray(BASYS.read_bytes())
    i = data.find(b"7a35tcpg236")
    data[i:i + 11] = b"7k70tfbg676"
    (tmp_path / "k7.bit").write_bytes(bytes(data))
    with pytest.raises(WrongBoardError) as e:
        validate_bitstream(tmp_path / "k7.bit", get_board("basys3"))
    assert e.value.board is None and "No supported virtual board" in str(e.value)
    with pytest.raises(BitfileError):
        validate_bitstream(tmp_path / "absent.bit", get_board("basys3"))
    (tmp_path / "cut.bit").write_bytes(BASYS.read_bytes()[:4000])
    with pytest.raises(BitfileError, match="incomplete"):
        validate_bitstream(tmp_path / "cut.bit", get_board("basys3"))


def test_board_lookup():
    assert board_for_part("xc7a35tcpg236-1").id == "basys3"
    assert board_for_part("7a100tcsg324").id == "nexys4ddr"
    assert board_for_part("xc7z020clg400") is None
    b = get_board("nexys4ddr")
    assert b.virtual_name == "virtual_nexys4ddr" and b.idcode == 0x03631093 and b.fpga_short == "XC7A100T"
    assert b.seven_segment.digits == 8 and b.resources["CPU_RESETN"].active_low
