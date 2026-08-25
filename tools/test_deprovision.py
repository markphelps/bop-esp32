#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mark Phelps
# SPDX-License-Identifier: Apache-2.0

"""Verify the safe Spotify disconnect and device-erasure flow."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hashlib
from io import StringIO
import os
import re
import struct
import sys
from types import ModuleType
from unittest.mock import Mock, patch

import deprovision
import detect_port
import device

USB_PORT = "/dev/usb-port"
MAC = "02:00:00:00:00:01"
IDENTITY = deprovision.DeviceIdentity(port=USB_PORT, mac=MAC)
ERASE_APPROVAL = f"ERASE {MAC}"


def run_main(inputs: list[str], **replacements: Mock) -> tuple[int, dict[str, Mock]]:
    actions: dict[str, Mock] = {
        "detect_usb_port": Mock(return_value=USB_PORT),
        "inspect_device": Mock(return_value=IDENTITY),
        "open_connected_apps": Mock(),
        "erase_verified_device": Mock(),
    }
    actions.update(replacements)
    values = iter(inputs)
    with (
        patch("builtins.input", side_effect=lambda _prompt: next(values)),
        patch.multiple(deprovision, **actions),
        redirect_stdout(StringIO()),
        redirect_stderr(StringIO()),
    ):
        result = deprovision.main()
    return result, actions


def test_missing_device_stops_before_account_action() -> None:
    result, actions = run_main([], detect_usb_port=Mock(side_effect=RuntimeError("No device")))
    assert result == 1
    actions["inspect_device"].assert_not_called()
    actions["open_connected_apps"].assert_not_called()
    actions["erase_verified_device"].assert_not_called()


def test_unrecognized_device_stops_before_account_action() -> None:
    result, actions = run_main(
        [], inspect_device=Mock(side_effect=RuntimeError("Not a Bop device"))
    )
    assert result == 1
    actions["open_connected_apps"].assert_not_called()
    actions["erase_verified_device"].assert_not_called()


def test_failed_account_confirmation_stops_before_erase() -> None:
    result, actions = run_main(["NOT REMOVED"])
    assert result == 1
    actions["erase_verified_device"].assert_not_called()


def test_refusal_stops_before_erase() -> None:
    result, actions = run_main(["REMOVED", "NO"])
    assert result == 1
    actions["erase_verified_device"].assert_not_called()


def test_approval_is_bound_to_device_mac() -> None:
    result, actions = run_main(["REMOVED", "ERASE 00:00:00:00:00:00"])
    assert result == 1
    actions["erase_verified_device"].assert_not_called()


def test_failed_device_operation_returns_failure() -> None:
    result, actions = run_main(
        ["REMOVED", ERASE_APPROVAL],
        erase_verified_device=Mock(side_effect=RuntimeError("erase failed")),
    )
    assert result == 1
    actions["erase_verified_device"].assert_called_once_with(IDENTITY)


def test_port_override_must_identify_an_espressif_usb_device() -> None:
    serial = ModuleType("serial")
    tools = ModuleType("serial.tools")
    list_ports = ModuleType("serial.tools.list_ports")
    setattr(
        list_ports,
        "comports",
        lambda: [type("Port", (), {"device": "/dev/usbmodem", "vid": detect_port.ESPRESSIF_VID})()],
    )
    setattr(serial, "tools", tools)
    setattr(tools, "list_ports", list_ports)

    with patch.dict(
        sys.modules,
        {"serial": serial, "serial.tools": tools, "serial.tools.list_ports": list_ports},
    ):
        with patch.dict(os.environ, {"BOP_PORT": "socket://device.example:3232"}):
            try:
                detect_port.detect_port()
            except RuntimeError as error:
                assert "not a connected Espressif USB serial port" in str(error)
            else:
                raise AssertionError("detect_port accepted a non-USB override")
        with patch.dict(os.environ, {"BOP_PORT": "/dev/usbmodem"}):
            assert detect_port.detect_port() == "/dev/usbmodem"


def test_device_inspection_uses_bop_identity_checks() -> None:
    result = subprocess_result(
        0,
        f'esptool output\n{deprovision.IDENTITY_MARKER}{{"mac": "{MAC}", "port": "{USB_PORT}"}}\n',
    )
    runner = Mock(return_value=result)
    with patch.object(deprovision, "run_esptool_python", runner):
        identity = deprovision.inspect_device(USB_PORT)

    assert identity == IDENTITY
    command, arguments = runner.call_args.args
    assert arguments == [USB_PORT, "0x8000", "0x1000", "0x9000", "0x6000", "0x10000"]
    assert 'loader.CHIP_NAME != "ESP32-S3"' in command
    assert 'entries.get("nvs") != expected_nvs' in command
    assert 'entries.get("factory") != expected_factory' in command
    assert 'project_name != "bop"' in command
    assert 'loader.read_mac("BASE_MAC")' in command


def subprocess_result(returncode: int, stdout: str = "", stderr: str = "") -> object:
    return type(
        "Result",
        (),
        {"returncode": returncode, "stdout": stdout, "stderr": stderr},
    )()


def partition_entry(
    name: str, entry_type: int, subtype: int, offset: int, size: int, flags: int = 0
) -> bytes:
    return struct.pack(
        "<HBBLL16sL",
        0x50AA,
        entry_type,
        subtype,
        offset,
        size,
        name.encode("ascii").ljust(16, b"\0"),
        flags,
    )


def bop_partition_table() -> bytes:
    entries = b"".join(
        [
            partition_entry("nvs", 1, 2, 0x9000, 0x6000),
            partition_entry("phy_init", 1, 1, 0xF000, 0x1000),
            partition_entry("factory", 0, 0, 0x10000, 0x800000),
        ]
    )
    digest = b"\xeb\xeb" + b"\xff" * 14 + hashlib.md5(entries).digest()
    return (entries + digest + b"\xff" * 32).ljust(0x1000, b"\xff")


def app_description() -> bytes:
    description = bytearray(b"\0" * 256)
    struct.pack_into("<L", description, 0, 0xABCD5432)
    description[48:51] = b"bop"
    return bytes(description)


class FakeLoader:
    CHIP_NAME = "ESP32-S3"

    def __init__(self, mac: str = MAC, nvs_image: bytes | None = None) -> None:
        self.mac = tuple(int(part, 16) for part in mac.split(":"))
        self.nvs_image = nvs_image if nvs_image is not None else b"\xff" * 0x6000
        self.erase_region = Mock()
        self.hard_reset = Mock()

    def read_mac(self, _mac_type: str) -> tuple[int, ...]:
        return self.mac

    def read_flash(self, offset: int, size: int) -> bytes:
        if (offset, size) == (0x8000, 0x1000):
            return bop_partition_table()
        if (offset, size) == (0x10020, 256):
            return app_description()
        if (offset, size) == (0x9000, 0x6000):
            return self.nvs_image
        raise AssertionError(f"Unexpected read: {offset:#x}, {size:#x}")


def run_erase_command(loader: FakeLoader, expected_mac: str = MAC) -> None:
    esptool = ModuleType("esptool")
    setattr(esptool, "detect_chip", Mock(return_value=Mock(run_stub=Mock(return_value=loader))))
    arguments = [
        "erase",
        USB_PORT,
        expected_mac,
        "0x8000",
        "0x1000",
        "0x9000",
        "0x6000",
        "0x10000",
        *deprovision.CREDENTIAL_KEYS,
    ]
    with patch.dict(sys.modules, {"esptool": esptool}), patch.object(sys, "argv", arguments):
        exec(deprovision.ERASE_VERIFIED_DEVICE_COMMAND, {"__name__": "__erase_test__"})


def test_firmware_check_matches_the_cmake_project_name() -> None:
    source = (device.project_root() / "firmware/CMakeLists.txt").read_text(encoding="utf-8")
    match = re.search(r"^\s*project\(([^)\s]+)\)", source, re.MULTILINE)
    assert match is not None, "firmware/CMakeLists.txt does not name a project"
    project_name = match.group(1)
    assert len(project_name.encode("ascii")) < 32
    assert f'project_name != "{project_name}"' in deprovision.DEVICE_VALIDATION_SOURCE


def test_erase_region_matches_partition_table() -> None:
    partition_line = next(
        line
        for line in (device.project_root() / "firmware/partitions.csv").read_text().splitlines()
        if line.strip().startswith("nvs,")
    )
    fields = [field.strip() for field in partition_line.split(",")]
    assert fields[3:5] == [deprovision.NVS_OFFSET, deprovision.NVS_SIZE]


def test_one_connection_rechecks_identity_erases_reads_and_resets() -> None:
    loader = FakeLoader()
    run_erase_command(loader)
    loader.erase_region.assert_called_once_with(0x9000, 0x6000)
    loader.hard_reset.assert_called_once_with()


def test_device_swap_stops_before_erase() -> None:
    loader = FakeLoader(mac="00:00:00:00:00:01")
    try:
        run_erase_command(loader)
    except SystemExit as error:
        assert "device changed after approval" in str(error).lower()
    else:
        raise AssertionError("a replacement device was accepted")
    loader.erase_region.assert_not_called()
    loader.hard_reset.assert_not_called()


def test_partition_digest_mismatch_stops_before_erase() -> None:
    loader = FakeLoader()
    original_read = loader.read_flash

    def corrupt_read(offset: int, size: int) -> bytes:
        image = original_read(offset, size)
        if (offset, size) == (0x8000, 0x1000):
            image = image[:16] + bytes([image[16] ^ 1]) + image[17:]
        return image

    loader.read_flash = corrupt_read  # type: ignore[method-assign]
    try:
        run_erase_command(loader)
    except SystemExit as error:
        assert "digest mismatch" in str(error).lower()
    else:
        raise AssertionError("a corrupt partition table was accepted")
    loader.erase_region.assert_not_called()


def test_readback_accepts_only_complete_erasure() -> None:
    bad_images = [
        b"\xff" * 20 + key.encode("ascii") + b"\xff" * (0x6000 - 20 - len(key))
        for key in deprovision.CREDENTIAL_KEYS
    ]
    bad_images.append(b"\xff" * 64 + b"\x00" + b"\xff" * (0x6000 - 65))
    bad_images.append(b"\xff" * (0x6000 - 1))
    for image in bad_images:
        loader = FakeLoader(nvs_image=image)
        try:
            run_erase_command(loader)
        except SystemExit:
            pass
        else:
            raise AssertionError("read-back accepted data that was not erased")
        loader.hard_reset.assert_not_called()


def test_device_operation_uses_one_esptool_process() -> None:
    runner = Mock(return_value=subprocess_result(0))
    with patch.object(deprovision, "run_esptool_python", runner):
        deprovision.erase_verified_device(IDENTITY)
    command, arguments = runner.call_args.args
    assert command.count("esptool.detect_chip") == 1
    assert command.index("actual_mac = inspect_bop_device") < command.index("loader.erase_region")
    assert command.index("loader.erase_region") < command.index("loader.read_flash(nvs_offset")
    assert command.index("loader.read_flash(nvs_offset") < command.index("loader.hard_reset")
    assert arguments[0:2] == [USB_PORT, MAC]


def test_firmware_shows_provisioning_before_spotify_starts() -> None:
    source = (device.project_root() / "firmware/main/app_main.c").read_text(encoding="utf-8")
    main = source[source.index("void app_main(void)") :]
    assert main.index("create_portal_screen(&portal_configuration)") < main.index(
        "if (!provisioned) {"
    )
    assert main.index("bop_screenshot_start(display)") < main.index("if (!provisioned) {")
    assert main.index("if (!provisioned) {") < main.index("xTaskCreatePinnedToCore")


def test_success_runs_verified_device_operation() -> None:
    result, actions = run_main(["REMOVED", ERASE_APPROVAL])
    assert result == 0
    actions["inspect_device"].assert_called_once_with(USB_PORT)
    actions["open_connected_apps"].assert_called_once_with()
    actions["erase_verified_device"].assert_called_once_with(IDENTITY)


def main() -> int:
    test_missing_device_stops_before_account_action()
    test_unrecognized_device_stops_before_account_action()
    test_failed_account_confirmation_stops_before_erase()
    test_refusal_stops_before_erase()
    test_approval_is_bound_to_device_mac()
    test_failed_device_operation_returns_failure()
    test_port_override_must_identify_an_espressif_usb_device()
    test_device_inspection_uses_bop_identity_checks()
    test_firmware_check_matches_the_cmake_project_name()
    test_erase_region_matches_partition_table()
    test_one_connection_rechecks_identity_erases_reads_and_resets()
    test_device_swap_stops_before_erase()
    test_partition_digest_mismatch_stops_before_erase()
    test_readback_accepts_only_complete_erasure()
    test_device_operation_uses_one_esptool_process()
    test_firmware_shows_provisioning_before_spotify_starts()
    test_success_runs_verified_device_operation()
    print("Deprovisioning safety checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
