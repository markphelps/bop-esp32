#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mark Phelps
# SPDX-License-Identifier: Apache-2.0

"""Verify screenshot framing and PNG output without a connected board."""

from __future__ import annotations

from contextlib import redirect_stderr
from io import BufferedWriter, StringIO
from pathlib import Path
import struct
import tempfile
from typing import Any, cast
from unittest.mock import patch
import zlib

import screenshot  # pyright: ignore[reportMissingImports]


class FakeSerial:
    """A serial connection that returns predetermined fragments."""

    def __init__(self, fragments: list[bytes]) -> None:
        self.fragments = iter(fragments)
        self.events: list[str] = []
        self.dtr = True
        self.rts = True
        self.port: str | None = None
        self.baudrate = 0
        self.timeout: float | None = None

    def close(self) -> None:
        self.events.append("close")

    def flush(self) -> None:
        self.events.append("flush")

    def open(self) -> None:
        self.events.append("open")

    def read(self, size: int = 1) -> bytes:
        self.events.append("read")
        return next(self.fragments, b"")

    def reset_input_buffer(self) -> None:
        self.events.append("reset")

    def write(self, data: bytes) -> int:
        self.events.append(f"write:{data.decode('ascii')}")
        return len(data)


def payload(pixel: bytes = b"\xf8\x00") -> bytes:
    return pixel * (screenshot.WIDTH * screenshot.HEIGHT)


def frame(
    body: bytes | None = None,
    *,
    version: int = screenshot.VERSION,
    status: int = 0,
    header_length: int = screenshot.HEADER_LENGTH,
    width: int = screenshot.WIDTH,
    height: int = screenshot.HEIGHT,
    pixel_format: int = screenshot.PIXEL_FORMAT_RGB565_BE,
    reserved: int = 0,
    payload_length: int = screenshot.PIXEL_BYTES,
    crc: int | None = None,
) -> bytes:
    body = payload() if body is None else body
    crc = zlib.crc32(body) & 0xFFFFFFFF if crc is None else crc
    header = screenshot.HEADER.pack(
        screenshot.MAGIC,
        version,
        status,
        header_length,
        width,
        height,
        pixel_format,
        reserved,
        payload_length,
        crc,
    )
    return header + body


def read_frame(fragments: list[bytes]) -> bytes:
    return screenshot.read_frame(FakeSerial(fragments))


def assert_refused(**fields: Any) -> None:
    try:
        read_frame([frame(**fields)])
    except RuntimeError:
        return
    raise AssertionError(f"accepted invalid frame fields: {fields}")


def png_chunks(image: bytes) -> dict[bytes, bytes]:
    assert image.startswith(b"\x89PNG\r\n\x1a\n")
    chunks: dict[bytes, bytes] = {}
    position = 8
    while position < len(image):
        length = struct.unpack(">I", image[position : position + 4])[0]
        kind = image[position + 4 : position + 8]
        start = position + 8
        chunks[kind] = image[start : start + length]
        position = start + length + 4
    return chunks


def test_fragmented_frame_and_split_magic() -> None:
    expected = payload()
    response = b"logs\nBO" + frame(expected)
    result = read_frame([response[:6], response[6:23], response[23:500], response[500:]])
    assert result == expected


def test_log_magic_before_a_frame_is_skipped() -> None:
    assert read_frame([b"log BOPS\x02\x00\x00 text\n" + frame()]) == payload()


def test_preheader_limit() -> None:
    assert read_frame([b"x" * screenshot.PRE_HEADER_LIMIT + frame()]) == payload()
    try:
        read_frame([b"x" * (screenshot.PRE_HEADER_LIMIT + 1) + frame()])
    except RuntimeError as error:
        assert "65536" in str(error)
    else:
        raise AssertionError("accepted 65537 pre-header bytes")


def test_timeout_and_eof_are_refused() -> None:
    connection = FakeSerial([])
    with patch.object(screenshot.time, "monotonic", side_effect=[0, 31]):
        try:
            screenshot.read_frame(connection)
        except RuntimeError as error:
            assert "Timed out" in str(error)
        else:
            raise AssertionError("accepted an empty serial stream")


def test_every_invalid_header_field_is_refused() -> None:
    assert_refused(version=2)
    assert_refused(status=1, width=0, height=0, pixel_format=0, payload_length=0, crc=0)
    assert_refused(header_length=23)
    assert_refused(width=screenshot.WIDTH - 1)
    assert_refused(height=screenshot.HEIGHT - 1)
    assert_refused(pixel_format=2)
    assert_refused(reserved=1)
    assert_refused(payload_length=screenshot.PIXEL_BYTES - 1)
    assert_refused(crc=0)


def test_device_error_frame_never_creates_an_image() -> None:
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "screen.png"
        serial = FakeSerial(
            [frame(status=2, width=0, height=0, pixel_format=0, payload_length=0, crc=0)]
        )
        with patch.object(screenshot.detect_port, "detect_port", return_value="/dev/usb"):
            try:
                screenshot.screenshot(output, lambda: serial)
            except RuntimeError:
                pass
            else:
                raise AssertionError("accepted a device error frame")
        assert not output.exists()
        assert serial.events[-1] == "close"


def test_opening_clears_reset_signals_before_the_port() -> None:
    serial = FakeSerial([frame()])
    assignments: list[tuple[str, object]] = []
    original = FakeSerial.__setattr__

    def record(self: FakeSerial, name: str, value: object) -> None:
        if name in {"dtr", "rts", "port"}:
            assignments.append((name, value))
        original(self, name, value)

    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "screen.png"
        with (
            patch.object(FakeSerial, "__setattr__", record),
            patch.object(screenshot.detect_port, "detect_port", return_value="/dev/usb"),
        ):
            screenshot.screenshot(output, lambda: serial)
    assert assignments[-3:] == [("dtr", False), ("rts", False), ("port", "/dev/usb")]
    assert serial.events[:3] == ["open", "reset", "write:s"]
    assert serial.events[-1] == "close"


def test_png_has_correct_dimensions_and_rgb_pixels() -> None:
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "screen.png"
        screenshot.write_png(output, payload(b"\x07\xe0"))
        chunks = png_chunks(output.read_bytes())
    assert struct.unpack(">IIBBBBB", chunks[b"IHDR"]) == (368, 448, 8, 2, 0, 0, 0)
    scanlines = zlib.decompress(chunks[b"IDAT"])
    assert scanlines[:4] == b"\x00\x00\xff\x00"


def test_destination_refusal_and_partial_write_cleanup() -> None:
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "screen.png"
        output.write_bytes(b"old")
        try:
            screenshot.write_png(output, payload())
        except RuntimeError:
            pass
        else:
            raise AssertionError("replaced an existing destination")
        assert output.read_bytes() == b"old"

        class BrokenOutput:
            def __init__(self, file: BufferedWriter) -> None:
                self.file = file

            def __enter__(self) -> BrokenOutput:
                return self

            def __exit__(self, exception_type: object, value: object, traceback: object) -> None:
                self.file.close()
                return None

            def write(self, data: bytes) -> int:
                self.file.write(data)
                raise OSError("disk full")

        original_open = Path.open

        def fail_after_create(path: Path, mode: str) -> BrokenOutput:
            return BrokenOutput(cast(BufferedWriter, original_open(path, mode)))

        output.unlink()
        with patch.object(Path, "open", fail_after_create):
            try:
                screenshot.write_png(output, payload())
            except OSError:
                pass
            else:
                raise AssertionError("accepted a failed PNG write")
        assert not output.exists()


def test_main_requires_one_new_output_path() -> None:
    errors = StringIO()
    with redirect_stderr(errors):
        assert screenshot.main([]) == 2
    assert "usage" in errors.getvalue()

    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "screen.png"
        output.touch()
        errors = StringIO()
        with redirect_stderr(errors):
            assert screenshot.main([str(output)]) == 1
        assert "already exists" in errors.getvalue()


def main() -> int:
    test_fragmented_frame_and_split_magic()
    test_log_magic_before_a_frame_is_skipped()
    test_preheader_limit()
    test_timeout_and_eof_are_refused()
    test_every_invalid_header_field_is_refused()
    test_device_error_frame_never_creates_an_image()
    test_opening_clears_reset_signals_before_the_port()
    test_png_has_correct_dimensions_and_rgb_pixels()
    test_destination_refusal_and_partial_write_cleanup()
    test_main_requires_one_new_output_path()
    print("Screenshot checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
