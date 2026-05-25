# radio.py
"""Serial protocol for AnyTone AT-D168UV calibration backup."""

import os
import time
from typing import Callable, Optional

import serial
import serial.tools.list_ports

# ── Configuration ────────────────────────────────────────────────────────────

EXPECTED_DEVICE_ID = b"ID168UV\x00V100\x00\x00\x06"

READ_REGIONS = [
    (0x02F60000, 2048, "calibration"),
    (0x02FA0000, 512, "device-info"),
]

READ_CHUNK = 128
BAUD_RATE = 921600
TIMEOUT = 3.0


# ── Types ────────────────────────────────────────────────────────────────────

LogCallback = Optional[Callable[[str, str], None]]        # (message, level)
ProgressCallback = Optional[Callable[[float, str], None]] # (fraction, label)


# ── Low-level helpers ────────────────────────────────────────────────────────

def send(port: serial.Serial, data: bytes) -> None:
    port.write(data)
    port.flush()


def recv_exact(port: serial.Serial, n: int) -> bytes:
    buf = b""
    deadline = time.monotonic() + TIMEOUT
    while len(buf) < n:
        if time.monotonic() > deadline:
            raise TimeoutError(
                f"Timeout waiting for {n} bytes; got {len(buf)}: {buf.hex()}"
            )
        chunk = port.read(n - len(buf))
        if chunk:
            buf += chunk
    return buf


def checksum(data: bytes) -> int:
    total = 0
    for b in data:
        total += b
    return total & 0xFF


# ── Session management ───────────────────────────────────────────────────────

def program(port: serial.Serial, on_log: LogCallback = None) -> None:
    if on_log:
        on_log("Starting programming mode...", "info")
    send(port, b"PROGRAM")
    ack = recv_exact(port, 3)
    if ack != bytes.fromhex("515806"):
        raise RuntimeError(f"Unexpected session-start response: {ack.hex()}")
    if on_log:
        on_log("Programming mode OK", "success")


def read_device_id(port: serial.Serial, on_log: LogCallback = None) -> bytes:
    if on_log:
        on_log("Reading device ID...", "info")
    send(port, bytes([0x02]))
    buf = recv_exact(port, 16)
    device_id = buf[:8] + buf[9:]
    if on_log:
        on_log("Device ID read OK", "success")
    return device_id


def end_session(port: serial.Serial, on_log: LogCallback = None) -> None:
    if on_log:
        on_log("Ending programming mode...", "info")
    send(port, b"END")
    ack = recv_exact(port, 1)
    if ack != b"\x06":
        raise RuntimeError(f"Unexpected END response: {ack.hex()}")
    if on_log:
        on_log("Session ended OK", "success")


# ── Data transfer ────────────────────────────────────────────────────────────

def read_block(port: serial.Serial, addr: int, length: int) -> bytes:
    cmd = bytes([
        0x52,
        (addr >> 24) & 0xFF,
        (addr >> 16) & 0xFF,
        (addr >> 8) & 0xFF,
        (addr) & 0xFF,
        length & 0xFF,
    ])
    send(port, cmd)

    resp = recv_exact(port, 8 + length)
    if resp[0] != 0x57:
        raise RuntimeError(f"Expected 'W' (0x57), got 0x{resp[0]:02X}")
    resp_addr = int.from_bytes(resp[1:5], "big")
    resp_len = resp[5]
    if resp_addr != addr or resp_len != length:
        raise RuntimeError(
            f"Address/length mismatch: expected {addr:#010x}/{length}, "
            f"got {resp_addr:#010x}/{resp_len}"
        )

    data = resp[6:6 + length]

    if resp[-1] != 0x06:
        raise RuntimeError(f"Missing ACK after data block (got 0x{resp[-1]:02X})")

    cksum = checksum(resp[1:-2])
    if cksum != resp[-2]:
        raise RuntimeError(
            f"Checksum mismatch at {addr:#010x}: "
            f"got 0x{cksum:02X}, expected 0x{resp[-2]:02X}"
        )

    return data


def dump_region(
    port: serial.Serial,
    addr: int,
    size: int,
    label: str = "",
    on_progress: ProgressCallback = None,
) -> bytes:
    result = bytearray()
    offset = 0
    while offset < size:
        chunk = min(READ_CHUNK, size - offset)
        data = read_block(port, addr, chunk)
        result.extend(data)
        offset += chunk
        addr += chunk

        if on_progress:
            on_progress(offset / size, label)

    return bytes(result)


# ── High-level entry point ───────────────────────────────────────────────────

def list_serial_ports() -> list[str]:
    return [p.device for p in serial.tools.list_ports.comports()]


def run_backup(
    port_name: str,
    file_prefix: str,
    on_progress: ProgressCallback = None,
    on_log: LogCallback = None,
) -> list[str]:
    """Run the full backup flow. Returns list of written file paths.

    Raises RuntimeError, TimeoutError, or serial.SerialException on failure.
    """
    if on_log:
        on_log(f"Opening {port_name} at {BAUD_RATE} baud...", "info")

    port = serial.Serial(
        port=port_name,
        baudrate=BAUD_RATE,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=0.1,
    )

    try:
        program(port, on_log)

        device_id = read_device_id(port, on_log)
        if device_id != EXPECTED_DEVICE_ID:
            raise RuntimeError(
                f"Device ID mismatch: expected {EXPECTED_DEVICE_ID!r}, "
                f"got {device_id!r}"
            )
        if on_log:
            on_log("Device ID verified: AT-D168UV", "success")

        all_data = bytearray()
        for base, size, label in READ_REGIONS:
            if on_log:
                on_log(
                    f"Reading {label} ({size} bytes) at {base:#010x}...",
                    "info",
                )
            region = dump_region(port, base, size, label, on_progress)
            all_data.extend(region)

        end_session(port, on_log)

    except Exception:
        port.close()
        raise

    port.close()

    written = []
    offset = 0
    for _, size, suffix in READ_REGIONS:
        name = file_prefix + "-" + suffix + ".bin"
        parent = os.path.dirname(name)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(name, "wb") as f:
            f.write(all_data[offset : offset + size])
        offset += size
        written.append(name)
        if on_log:
            on_log(f"Wrote {name}", "success")

    return written
