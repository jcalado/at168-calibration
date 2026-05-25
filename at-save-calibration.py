#!/usr/bin/env python3
"""
Usage: python at-save-calibration.py -p COM3 -f <prefix>
"""

import argparse
import sys

import serial

import radio


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dump radio memory regions to a binary file."
    )
    parser.add_argument(
        "-p", "--port", required=True,
        help="Serial port (e.g. COM3 or /dev/ttyUSB0)",
    )
    parser.add_argument(
        "-f", "--file", required=True,
        help="Output file prefix",
    )
    args = parser.parse_args()

    def on_log(message: str, level: str) -> None:
        prefix = "  " if level != "error" else "ERROR: "
        print(f"{prefix}{message}")

    def on_progress(fraction: float, label: str) -> None:
        filled = int(40 * fraction)
        bar = "█" * filled + "░" * (40 - filled)
        pct = fraction * 100
        print(f"\r    [{bar}] {pct:5.1f}%", end="", flush=True)
        if fraction >= 1.0:
            print()

    try:
        radio.run_backup(args.port, args.file, on_progress, on_log)
    except (RuntimeError, TimeoutError, serial.SerialException) as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
