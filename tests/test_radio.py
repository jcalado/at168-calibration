import radio


def test_checksum_empty():
    assert radio.checksum(b"") == 0


def test_checksum_single_byte():
    assert radio.checksum(b"\x42") == 0x42


def test_checksum_overflow_wraps():
    assert radio.checksum(b"\xFF\x02") == 0x01


def test_checksum_known_sequence():
    data = bytes([0x10, 0x20, 0x30])
    assert radio.checksum(data) == 0x60


import struct
from unittest.mock import MagicMock


def make_read_response(addr: int, data: bytes) -> bytes:
    """Build a valid read_block response frame."""
    length = len(data)
    header = struct.pack(">BIB", 0x57, addr, length)
    payload = header[1:] + data  # everything after 0x57 for checksum
    cksum = radio.checksum(payload)
    return header + data + bytes([cksum, 0x06])


def test_read_block_valid_response():
    addr = 0x02F60000
    payload = bytes(range(128))
    response = make_read_response(addr, payload)

    mock_port = MagicMock()
    mock_port.write = MagicMock()
    mock_port.flush = MagicMock()
    mock_port.read = MagicMock(return_value=response)

    result = radio.read_block(mock_port, addr, 128)
    assert result == payload


def test_read_block_wrong_command_byte():
    mock_port = MagicMock()
    mock_port.write = MagicMock()
    mock_port.flush = MagicMock()
    bad_resp = b"\x00" + b"\x00" * 135  # wrong first byte
    mock_port.read = MagicMock(return_value=bad_resp)

    try:
        radio.read_block(mock_port, 0x02F60000, 128)
        assert False, "Should have raised RuntimeError"
    except RuntimeError as e:
        assert "0x57" in str(e)


def test_read_block_checksum_mismatch():
    addr = 0x02F60000
    payload = bytes(range(128))
    response = bytearray(make_read_response(addr, payload))
    response[-2] = (response[-2] + 1) & 0xFF  # corrupt checksum

    mock_port = MagicMock()
    mock_port.write = MagicMock()
    mock_port.flush = MagicMock()
    mock_port.read = MagicMock(return_value=bytes(response))

    try:
        radio.read_block(mock_port, addr, 128)
        assert False, "Should have raised RuntimeError"
    except RuntimeError as e:
        assert "Checksum" in str(e)
