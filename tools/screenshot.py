#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mark Phelps
# SPDX-License-Identifier: Apache-2.0

"""Save one Bop display snapshot from USB serial as a PNG file."""

from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path
import struct
import sys
import time
from typing import Protocol, cast
import zlib

import detect_port

MAGIC = b"BOPS"
VERSION = 1
HEADER = struct.Struct("<4sBBHHHHHII")
HEADER_LENGTH = HEADER.size
WIDTH = 368
HEIGHT = 448
PIXEL_FORMAT_RGB565_BE = 1
PIXEL_BYTES = WIDTH * HEIGHT * 2
PRE_HEADER_LIMIT = 65536
FRAME_TIMEOUT_SECONDS = 30
REEXEC_GUARD = "BOP_SCREENSHOT_PYSERIAL_REEXEC"


class ScreenshotNotReadyError(RuntimeError):
    """The device has not finished its first display mirror refresh."""


class SerialConnection(Protocol):
    """The subset of pyserial used for one screenshot request."""

    dtr: bool
    rts: bool
    port: str | None
    baudrate: int
    timeout: float | None

    def close(self) -> None: ...

    def flush(self) -> None: ...

    def open(self) -> None: ...

    def read(self, size: int = 1) -> bytes: ...

    def reset_input_buffer(self) -> None: ...

    def write(self, data: bytes) -> int: ...


def reexec_with_esptool_python(arguments: list[str]) -> None:
    """Restart with esptool's Python interpreter, which includes pyserial."""
    python = detect_port.esptool_python()
    if python is None or os.environ.get(REEXEC_GUARD) == "1":
        raise RuntimeError("pyserial is unavailable. Run `mise install` to install esptool.")
    environment = os.environ.copy()
    environment[REEXEC_GUARD] = "1"
    os.execve(str(python), [str(python), str(Path(__file__).resolve()), *arguments], environment)


def serial_factory(arguments: list[str]) -> Callable[[], SerialConnection]:
    """Import pyserial, restarting under the esptool interpreter when needed."""
    try:
        import serial  # pyright: ignore[reportMissingModuleSource]
    except ModuleNotFoundError:
        reexec_with_esptool_python(arguments)
        raise AssertionError("The esptool Python re-execution returned")
    return cast(Callable[[], SerialConnection], serial.Serial)


def open_serial(factory: Callable[[], SerialConnection]) -> SerialConnection:
    """Open the Bop port without asserting reset control signals."""
    connection = factory()
    connection.dtr = False
    connection.rts = False
    try:
        connection.port = detect_port.detect_port()
        connection.baudrate = 115200
        connection.timeout = 1
        connection.open()
    except BaseException:
        connection.close()
        raise
    return connection


def read_more(connection: SerialConnection, deadline: float) -> bytes:
    """Read one serial chunk, or stop when the complete-frame deadline expires."""
    data = connection.read(4096)
    if data:
        return data
    if time.monotonic() >= deadline:
        raise RuntimeError("Timed out while waiting for a complete screenshot frame")
    return b""


def validate_header(header: bytes) -> int:
    """Validate a version-1 success header and return its payload length."""
    (
        magic,
        version,
        status,
        header_length,
        width,
        height,
        pixel_format,
        reserved,
        payload_length,
        expected_crc,
    ) = HEADER.unpack(header)
    if magic != MAGIC:
        raise RuntimeError("The screenshot response has invalid magic")
    if version != VERSION:
        raise RuntimeError(f"The screenshot response has unsupported version {version}")
    if status == 1:
        raise ScreenshotNotReadyError("The device display mirror is not ready")
    if status != 0:
        raise RuntimeError(f"The device refused the screenshot request with status {status}")
    if header_length != HEADER_LENGTH:
        raise RuntimeError("The screenshot response has an invalid header length")
    if width != WIDTH or height != HEIGHT:
        raise RuntimeError("The screenshot response has unexpected dimensions")
    if pixel_format != PIXEL_FORMAT_RGB565_BE:
        raise RuntimeError("The screenshot response has an unsupported pixel format")
    if reserved != 0:
        raise RuntimeError("The screenshot response has a nonzero reserved value")
    if payload_length != PIXEL_BYTES:
        raise RuntimeError("The screenshot response has an unexpected payload length")
    return expected_crc


def read_frame(connection: SerialConnection, deadline: float | None = None) -> bytes:
    """Read and validate one framed screenshot response from a noisy serial stream."""
    deadline = time.monotonic() + FRAME_TIMEOUT_SECONDS if deadline is None else deadline
    buffer = bytearray()
    skipped = 0

    while True:
        magic_position = buffer.find(MAGIC)
        if magic_position >= 0:
            skipped += magic_position
            if skipped > PRE_HEADER_LIMIT:
                raise RuntimeError(
                    "The screenshot response has more than 65536 bytes before its header"
                )
            del buffer[:magic_position]
            while len(buffer) < 8:
                data = read_more(connection, deadline)
                if data:
                    buffer.extend(data)
            version, header_length = buffer[4], struct.unpack_from("<H", buffer, 6)[0]
            if version == VERSION and header_length == HEADER_LENGTH:
                break
            del buffer[0]
            skipped += 1
            continue

        keep = min(len(buffer), len(MAGIC) - 1)
        skipped += len(buffer) - keep
        if skipped > PRE_HEADER_LIMIT:
            raise RuntimeError(
                "The screenshot response has more than 65536 bytes before its header"
            )
        if len(buffer) > keep:
            del buffer[:-keep]
        data = read_more(connection, deadline)
        if data:
            buffer.extend(data)

    while len(buffer) < HEADER_LENGTH:
        data = read_more(connection, deadline)
        if data:
            buffer.extend(data)

    expected_crc = validate_header(bytes(buffer[:HEADER_LENGTH]))
    del buffer[:HEADER_LENGTH]
    while len(buffer) < PIXEL_BYTES:
        data = read_more(connection, deadline)
        if data:
            buffer.extend(data)

    payload = bytes(buffer[:PIXEL_BYTES])
    actual_crc = zlib.crc32(payload) & 0xFFFFFFFF
    if actual_crc != expected_crc:
        raise RuntimeError("The screenshot response has an invalid CRC")
    return payload


def rgb565_to_png_rows(payload: bytes) -> bytes:
    """Convert big-endian RGB565 pixels to unfiltered, truecolor PNG rows."""
    rows = bytearray((WIDTH * 3 + 1) * HEIGHT)
    source = 0
    destination = 0
    for _ in range(HEIGHT):
        rows[destination] = 0
        destination += 1
        for _ in range(WIDTH):
            value = (payload[source] << 8) | payload[source + 1]
            source += 2
            rows[destination] = ((value >> 11) & 0x1F) * 255 // 0x1F
            rows[destination + 1] = ((value >> 5) & 0x3F) * 255 // 0x3F
            rows[destination + 2] = (value & 0x1F) * 255 // 0x1F
            destination += 3
    return bytes(rows)


def png_chunk(kind: bytes, data: bytes) -> bytes:
    """Create one PNG chunk with its CRC."""
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


def write_png(path: Path, payload: bytes) -> None:
    """Write a screenshot PNG without replacing an existing destination."""
    if path.exists():
        raise RuntimeError(f"The output file already exists: {path}")
    header = struct.pack(">IIBBBBB", WIDTH, HEIGHT, 8, 2, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", header)
    png += png_chunk(b"IDAT", zlib.compress(rgb565_to_png_rows(payload)))
    png += png_chunk(b"IEND", b"")
    created = False
    try:
        with path.open("xb") as output:
            created = True
            output.write(png)
    except OSError:
        if created:
            path.unlink(missing_ok=True)
        raise


def screenshot(path: Path, factory: Callable[[], SerialConnection]) -> None:
    """Request a screenshot and write it after the complete frame validates."""
    if path.exists():
        raise RuntimeError(f"The output file already exists: {path}")
    connection = open_serial(factory)
    deadline = time.monotonic() + FRAME_TIMEOUT_SECONDS
    try:
        connection.reset_input_buffer()
        while True:
            if time.monotonic() >= deadline:
                raise RuntimeError("Timed out while waiting for a complete screenshot frame")
            if connection.write(b"s") != 1:
                raise RuntimeError("Could not send the screenshot request")
            connection.flush()
            try:
                write_png(path, read_frame(connection, deadline))
                return
            except ScreenshotNotReadyError:
                time.sleep(0.1)
    finally:
        connection.close()


def main(arguments: list[str] | None = None) -> int:
    """Run the screenshot command and report any actionable error."""
    arguments = sys.argv[1:] if arguments is None else arguments
    if len(arguments) != 1:
        print("usage: screenshot.py <output.png>", file=sys.stderr)
        return 2
    path = Path(arguments[0])
    if path.exists():
        print(f"screenshot failed: The output file already exists: {path}", file=sys.stderr)
        return 1

    try:
        screenshot(path, serial_factory(arguments))
    except (OSError, RuntimeError, ValueError) as error:
        print(f"screenshot failed: {error}", file=sys.stderr)
        return 1
    print(f"Saved {path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Canceled.", file=sys.stderr)
        raise SystemExit(130)
