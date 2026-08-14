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

ROOT = Path(__file__).resolve().parent.parent


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


def error_frame(status: int) -> bytes:
    return frame(status=status, width=0, height=0, pixel_format=0, payload_length=0, crc=0)[
        : screenshot.HEADER_LENGTH
    ]


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


def firmware_source() -> str:
    return (ROOT / "firmware/main/screenshot.c").read_text(encoding="utf-8")


def firmware_function(name: str, following: str) -> str:
    source = firmware_source()
    start = source.index(name)
    return source[start : source.index(following, start)]


def test_firmware_uses_bounded_serial_writes() -> None:
    source = firmware_source()
    assert "#define BOP_SCREENSHOT_SERIAL_WRITE_SIZE 1024U" in source
    assert "#define BOP_SCREENSHOT_SERIAL_BUDGET_MS" in source
    writer = firmware_function("static bool write_serial_bytes", "static bool send_response")
    assert "write_size" in writer
    # Each chunk waits for what is left of the response budget, never forever.
    assert "usb_serial_jtag_write_bytes(source + offset, write_size, wait)" in writer
    assert "wait = remaining_budget(start)" in writer
    assert "portMAX_DELAY" not in writer


def test_one_serial_budget_covers_a_whole_screenshot_frame() -> None:
    """The budget bounds the mutex hold, so it starts once for the whole frame.

    A budget taken for each chunk still holds the serial output mutex for
    minutes across the 322 chunks of one payload, and every task that writes a
    log line waits behind that mutex.
    """
    response = firmware_function("static bool send_response", "static void send_screenshot")
    assert response.count("xTaskGetTickCount()") == 1
    assert response.index("xSemaphoreTake(serial_output_mutex, portMAX_DELAY)") < response.index(
        "xTaskGetTickCount()"
    )
    assert response.count("write_serial_bytes(") == 2
    assert response.count(", start)") == 2
    assert "usb_serial_jtag_wait_tx_done(remaining_budget(start))" in response
    # The mutex take is the only unbounded wait left in the response path.
    assert response.count("portMAX_DELAY") == 1

    budget = firmware_function(
        "static TickType_t remaining_budget", "static bool write_serial_bytes"
    )
    assert "pdMS_TO_TICKS(BOP_SCREENSHOT_SERIAL_BUDGET_MS)" in budget
    assert "elapsed < budget ? budget - elapsed : 0" in budget


def test_an_abandoned_frame_releases_the_mutex_and_is_reported_once() -> None:
    response = firmware_function("static bool send_response", "static void send_screenshot")
    give = "xSemaphoreGive(serial_output_mutex);"
    assert response.count(give) == 1
    assert response.index("usb_serial_jtag_wait_tx_done") < response.index(give)
    assert response.index(give) < response.index("return sent;")
    assert "ESP_LOG" not in response

    sender = firmware_function("static void send_screenshot", "static void handle_spotify_command")
    assert sender.count("ESP_LOG") == 1
    assert "if (!send_response(status, crc)) {" in sender
    assert "abandoned" in sender


def test_the_log_hook_is_never_installed_over_a_null_forward_pointer() -> None:
    """The hook goes live before the returned handler is stored.

    esp_log_set_vprintf installs serial_log_vprintf and only then returns the
    previous handler, and bop_screenshot_init runs after the LVGL task starts
    on core 1. A log line in that window calls the forward pointer, so the
    pointer needs a working value before the hook goes live.
    """
    source = firmware_source()
    declaration = "static vprintf_like_t original_vprintf = vprintf;"
    assert declaration in source
    assert source.index(declaration) < source.index("esp_log_set_vprintf(")
    initialization = firmware_function(
        "esp_err_t bop_screenshot_init", "esp_err_t bop_screenshot_start"
    )
    assert "vprintf_like_t previous = esp_log_set_vprintf(serial_log_vprintf);" in initialization
    assert "if (previous != NULL) {" in initialization


def test_missing_mirror_buffers_fail_initialization_but_keep_the_serial_task() -> None:
    """A failed PSRAM allocation is reported once at startup and stops nothing.

    Without the mirror every capture waits the full one-second refresh timeout,
    logs a refresh warning that names the wrong cause, and answers the
    no-memory status. An ESP_OK return kept that persistent state off the log.
    The serial task still starts, because status 2 and the n, b, and t keys are
    host-visible behaviors that work without the mirror.
    """
    initialization = firmware_function(
        "esp_err_t bop_screenshot_init", "esp_err_t bop_screenshot_start"
    )
    allocation = initialization[initialization.index("heap_caps_malloc") :]
    assert "return ESP_ERR_NO_MEM;" in allocation
    assert allocation.count("return ESP_OK;") == 1
    assert allocation.index("return ESP_ERR_NO_MEM;") < allocation.index("return ESP_OK;")
    # The globals take the pointers only after both allocations succeed, so the
    # failure path never frees a buffer the LVGL task can still reach through a
    # global. bop_screenshot_mirror_area tests mirror_buffer outside the mutex.
    failure = allocation[: allocation.index("return ESP_ERR_NO_MEM;")]
    assert "mirror_buffer" not in failure
    assert "staging_buffer" not in failure
    assert failure.count("heap_caps_free(") == 2
    published = allocation[allocation.index("return ESP_ERR_NO_MEM;") :]
    assert published.index("mirror_buffer = ") < published.index("return ESP_OK;")
    assert published.index("staging_buffer = ") < published.index("return ESP_OK;")

    start = firmware_function("esp_err_t bop_screenshot_start", "void bop_screenshot_mirror_area")
    # The mutexes are the one state the task cannot run without: every response
    # path takes them. The buffers are not, so the task creation sits outside
    # the buffer condition. The refusal has to belong to the mutex guard, not
    # to some other guard that happens to return the same code.
    assert (
        "    if (mirror_mutex == NULL || serial_output_mutex == NULL) {\n"
        "        return ESP_ERR_INVALID_STATE;\n"
        "    }\n"
    ) in start
    assert start.count("return ESP_ERR_INVALID_STATE;") == 1
    assert start.index("return ESP_ERR_INVALID_STATE;") < start.index("if (mirror_buffer != NULL")
    assert "    }\n    if (xTaskCreatePinnedToCore(" in start
    # Nesting is not the whole requirement. An early return added anywhere ahead
    # of the task creation leaves the creation unnested and still takes status 2
    # and the n, b, and t keys away from a board that ran out of PSRAM. So the
    # span runs from the head of the function, not from the mutex guard: a
    # refusal added above that guard reintroduces the same regression. Exactly
    # three returns belong in the span — the null-display guard, the mutex
    # guard, and the refresh timer failure inside the buffer block.
    before_task = start[
        start.index("if (display == NULL)") : start.index("if (xTaskCreatePinnedToCore(")
    ]
    assert before_task.count("return ") == 3
    assert before_task.count("return ESP_ERR_INVALID_ARG;") == 1
    assert before_task.count("return ESP_ERR_NO_MEM;") == 1

    source = (ROOT / "firmware/main/app_main.c").read_text(encoding="utf-8")
    main = source[source.index("void app_main(void)") :]
    # Anchored on the call, because the guard text repeats after the start call:
    # an index on the guard alone still passes when this report is deleted.
    call = "esp_err_t screenshot_error = bop_screenshot_init();"
    assert call in main
    after_call = main[main.index(call) + len(call) :]
    report = after_call[: after_call.index("\n    }")]
    assert report.lstrip().startswith("if (screenshot_error != ESP_OK) {")
    assert report.count("ESP_LOG") == 1
    assert report.count("esp_err_to_name(screenshot_error)") == 1
    # The block reports and does nothing else, and no later refusal stands
    # between it and the start call either. Both take status 2 and the n, b,
    # and t keys away from the board that ran out of PSRAM. Exactly one return
    # belongs in that span, the LVGL lock guard, and it fails the display for
    # every caller rather than for the screenshot alone.
    assert "return" not in report
    # The start is a whole statement, so the text below is the whole call. A
    # ternary that withholds the call on screenshot_error passes the "if ("
    # count further down, because it adds no "if (" of its own.
    start_call = "screenshot_error = bop_screenshot_start(display);"
    assert start_call in after_call
    before_start = after_call[: after_call.index(start_call)]
    assert before_start.count("return") == 1
    assert "if (!bsp_display_lock(0)) {" in before_start
    # No condition on the initialization result stands in front of the start.
    condition = main[main.index("if (ui_error == ESP_OK") :]
    assert condition[: condition.index("\n")] == "if (ui_error == ESP_OK) {"
    assert condition[: condition.index("bop_screenshot_start(display)")].count("if (") == 1
    assert main.index("bop_screenshot_init()") < main.index("bop_screenshot_start(display)")


def test_no_initialization_failure_leaks_a_mutex() -> None:
    """Both early returns free whichever mutex exists.

    The recursive mirror mutex can be created while the serial output mutex
    fails, so a bare pair of deletes either leaks the survivor or hands
    vSemaphoreDelete a NULL handle.
    """
    cleanup = firmware_function("static void delete_mutexes", "static int serial_log_vprintf")
    # Each delete sits inside its own guard and clears its handle. Without the
    # clear, bop_screenshot_start sees two dangling handles, starts the task,
    # and the task takes a freed semaphore on the first byte it reads.
    assert (
        "    if (mirror_mutex != NULL) {\n"
        "        vSemaphoreDelete(mirror_mutex);\n"
        "        mirror_mutex = NULL;\n"
        "    }\n"
    ) in cleanup
    assert (
        "    if (serial_output_mutex != NULL) {\n"
        "        vSemaphoreDelete(serial_output_mutex);\n"
        "        serial_output_mutex = NULL;\n"
        "    }\n"
    ) in cleanup
    assert cleanup.count("vSemaphoreDelete(") == 2

    initialization = firmware_function(
        "esp_err_t bop_screenshot_init", "esp_err_t bop_screenshot_start"
    )
    assert initialization.count("delete_mutexes();") == 2
    assert "vSemaphoreDelete(" not in initialization
    creation_failure = initialization[
        initialization.index("if (mirror_mutex == NULL || serial_output_mutex == NULL) {") :
    ]
    assert (
        "delete_mutexes();" in creation_failure[: creation_failure.index("return ESP_ERR_NO_MEM;")]
    )
    driver_failure = initialization[initialization.index("usb_serial_jtag_driver_install") :]
    assert "delete_mutexes();" in driver_failure[: driver_failure.index("return error;")]


def test_screenshot_refresh_runs_in_the_lvgl_task() -> None:
    source = (ROOT / "firmware/main/screenshot.c").read_text(encoding="utf-8")
    timer_start = source.index("static void refresh_timer")
    timer_end = source.index("static bool request_refresh", timer_start)
    timer = source[timer_start:timer_end]
    task_start = source.index("static void screenshot_task")
    task_end = source.index("esp_err_t bop_screenshot_init", task_start)
    task = source[task_start:task_end]
    assert "lv_obj_invalidate(lv_screen_active());" in timer
    assert "ulTaskNotifyTake" in source
    assert "xTaskNotifyGive(screenshot_task_handle);" in source
    assert "request_refresh()" in task
    assert task.index("request_refresh()") < task.index("send_screenshot();")
    assert "lv_refr_now" not in source


def test_refresh_timer_is_created_before_the_main_task_unlocks_lvgl() -> None:
    source = (ROOT / "firmware/main/app_main.c").read_text(encoding="utf-8")
    start = source.index("void app_main(void)")
    main = source[start:]
    assert main.index("bop_screenshot_start(display)") < main.index("bsp_display_unlock();")
    assert main.index("bop_screenshot_start(display)") < main.index("if (!provisioned) {")


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


def test_not_ready_response_is_distinct_from_other_device_errors() -> None:
    try:
        read_frame([error_frame(1)])
    except screenshot.ScreenshotNotReadyError:
        pass
    else:
        raise AssertionError("did not identify the not-ready response")


def test_not_ready_response_is_retried() -> None:
    serial = FakeSerial([error_frame(1), frame()])
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "screen.png"
        with (
            patch.object(screenshot.detect_port, "detect_port", return_value="/dev/usb"),
            patch.object(screenshot.time, "sleep"),
        ):
            screenshot.screenshot(output, lambda: serial)
        assert output.is_file()
    assert serial.events.count("write:s") == 2


def test_corrupted_frame_is_distinct_from_a_device_error() -> None:
    try:
        read_frame([frame(crc=0)])
    except screenshot.ScreenshotCorruptFrameError:
        pass
    else:
        raise AssertionError("did not identify the corrupted frame")

    try:
        read_frame([error_frame(2)])
    except (screenshot.ScreenshotNotReadyError, screenshot.ScreenshotCorruptFrameError):
        raise AssertionError("treated a device error frame as a retryable frame")
    except RuntimeError:
        pass
    else:
        raise AssertionError("accepted a device error frame")


def test_corrupted_frame_is_retried() -> None:
    corrupted = bytearray(frame())
    corrupted[screenshot.HEADER_LENGTH] ^= 0xFF
    serial = FakeSerial([bytes(corrupted), frame()])
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "screen.png"
        with (
            patch.object(screenshot.detect_port, "detect_port", return_value="/dev/usb"),
            patch.object(screenshot.time, "sleep"),
        ):
            screenshot.screenshot(output, lambda: serial)
        chunks = png_chunks(output.read_bytes())
    assert serial.events.count("write:s") == 2
    assert serial.events.count("reset") == 2
    assert serial.events.index("reset") < serial.events.index("write:s")
    scanlines = zlib.decompress(chunks[b"IDAT"])
    assert scanlines[:4] == b"\x00\xff\x00\x00"


def test_device_error_frame_never_creates_an_image() -> None:
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "screen.png"
        serial = FakeSerial([error_frame(2)])
        with patch.object(screenshot.detect_port, "detect_port", return_value="/dev/usb"):
            try:
                screenshot.screenshot(output, lambda: serial)
            except RuntimeError:
                pass
            else:
                raise AssertionError("accepted a device error frame")
        assert not output.exists()
        assert serial.events.count("write:s") == 1
        assert serial.events[-1] == "close"


def test_opening_never_reaches_the_reset_line_state() -> None:
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
    assert assignments == [
        ("dtr", True),
        ("rts", True),
        ("port", "/dev/usb"),
        ("rts", False),
        ("dtr", False),
    ]
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
    test_firmware_uses_bounded_serial_writes()
    test_one_serial_budget_covers_a_whole_screenshot_frame()
    test_an_abandoned_frame_releases_the_mutex_and_is_reported_once()
    test_the_log_hook_is_never_installed_over_a_null_forward_pointer()
    test_missing_mirror_buffers_fail_initialization_but_keep_the_serial_task()
    test_no_initialization_failure_leaks_a_mutex()
    test_screenshot_refresh_runs_in_the_lvgl_task()
    test_refresh_timer_is_created_before_the_main_task_unlocks_lvgl()
    test_fragmented_frame_and_split_magic()
    test_log_magic_before_a_frame_is_skipped()
    test_preheader_limit()
    test_timeout_and_eof_are_refused()
    test_every_invalid_header_field_is_refused()
    test_not_ready_response_is_distinct_from_other_device_errors()
    test_not_ready_response_is_retried()
    test_corrupted_frame_is_distinct_from_a_device_error()
    test_corrupted_frame_is_retried()
    test_device_error_frame_never_creates_an_image()
    test_opening_never_reaches_the_reset_line_state()
    test_png_has_correct_dimensions_and_rgb_pixels()
    test_destination_refusal_and_partial_write_cleanup()
    test_main_requires_one_new_output_path()
    print("Screenshot checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
