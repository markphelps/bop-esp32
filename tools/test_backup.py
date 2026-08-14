#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mark Phelps
# SPDX-License-Identifier: Apache-2.0

"""Verify that a provisioned device or image cannot produce, keep, or restore a backup."""

from __future__ import annotations

import ast
from contextlib import redirect_stderr, redirect_stdout
import hashlib
from io import StringIO
from pathlib import Path
import sys
import tempfile
import tomllib
from types import ModuleType
from unittest.mock import Mock, patch

import backup_flash
import device
import flash
import restore

USB_PORT = "/dev/usb-port"
MAC = "02:00:00:00:00:01"
IDENTITY = device.DeviceIdentity(port=USB_PORT, mac=MAC)
RESTORE_APPROVAL = f"RESTORE {MAC}"
NVS_OFFSET = int(device.NVS_OFFSET, 0)
NVS_SIZE = int(device.NVS_SIZE, 0)


def subprocess_result(returncode: int, stdout: str = "", stderr: str = "") -> object:
    return type("Result", (), {"returncode": returncode, "stdout": stdout, "stderr": stderr})()


def flash_image(keys: tuple[str, ...] = (), size: int = device.FLASH_SIZE_BYTES) -> bytes:
    """Build a flash image whose NVS region holds the named credential keys."""
    body = bytearray(b"\xff" * size)
    position = NVS_OFFSET
    for key in keys:
        encoded = key.encode("ascii")
        body[position : position + len(encoded)] = encoded
        position += len(encoded) + 8
    return bytes(body)


def write_backup(
    directory: Path, image_bytes: bytes, digest: str | None = None
) -> tuple[Path, Path]:
    image = directory / "factory.bin"
    checksum = directory / "factory.bin.sha256"
    image.write_bytes(image_bytes)
    if digest is None:
        digest = hashlib.sha256(image_bytes).hexdigest()
    checksum.write_text(f"{digest}  {image.name}\n", encoding="ascii")
    return image, checksum


def run_backup_main(root: Path, **replacements: Mock) -> tuple[int, dict[str, Mock], str]:
    actions: dict[str, Mock] = {
        "project_root": Mock(return_value=root),
        "esptool_path": Mock(return_value="/usr/bin/esptool"),
        "detect_usb_port": Mock(return_value=USB_PORT),
        "probe_device_credentials": Mock(return_value=[]),
        "read_full_flash": Mock(),
    }
    actions.update(replacements)
    errors = StringIO()
    with (
        patch.multiple(backup_flash, **actions),
        redirect_stdout(StringIO()),
        redirect_stderr(errors),
    ):
        result = backup_flash.main()
    return result, actions, errors.getvalue()


def run_restore_main(
    root: Path, inputs: list[str], **replacements: Mock
) -> tuple[int, dict[str, Mock]]:
    actions: dict[str, Mock] = {
        "project_root": Mock(return_value=root),
        "detect_usb_port": Mock(return_value=USB_PORT),
        "read_device_identity": Mock(return_value=IDENTITY),
        "write_verified_flash": Mock(),
    }
    actions.update(replacements)
    values = iter(inputs)
    with (
        patch("builtins.input", side_effect=lambda _prompt: next(values)),
        patch.multiple(restore, **actions),
        redirect_stdout(StringIO()),
        redirect_stderr(StringIO()),
    ):
        result = restore.main()
    return result, actions


def test_probe_reads_only_the_nvs_region() -> None:
    runner = Mock(return_value=subprocess_result(0, f'{device.NVS_PROBE_MARKER}{{"keys": []}}\n'))
    with patch.object(device, "run_esptool_python", runner):
        assert device.probe_device_credentials(USB_PORT) == []
    command, arguments = runner.call_args.args
    assert arguments == [USB_PORT, "0x9000", "0x6000", *device.CREDENTIAL_KEYS]
    assert "loader.read_flash(nvs_offset, nvs_size)" in command
    assert "0x1000000" not in command
    # The probe must clear its buffer and must never print a value.
    assert "image[position] = 0" in command
    assert command.index("found = sorted") < command.index("image[position] = 0")


def test_probe_reports_each_credential_key() -> None:
    esptool = ModuleType("esptool")
    loader = Mock()
    loader.read_flash = Mock(
        return_value=flash_image(device.CREDENTIAL_KEYS)[NVS_OFFSET : NVS_OFFSET + NVS_SIZE]
    )
    setattr(esptool, "detect_chip", Mock(return_value=Mock(run_stub=Mock(return_value=loader))))
    arguments = ["probe", USB_PORT, "0x9000", "0x6000", *device.CREDENTIAL_KEYS]
    output = StringIO()
    with (
        patch.dict(sys.modules, {"esptool": esptool}),
        patch.object(sys, "argv", arguments),
        redirect_stdout(output),
    ):
        exec(device.PROBE_NVS_COMMAND, {"__name__": "__probe_test__"})
    line = output.getvalue().strip()
    assert line.startswith(device.NVS_PROBE_MARKER)
    assert sorted(device.CREDENTIAL_KEYS) == sorted(
        __import__("json").loads(line.removeprefix(device.NVS_PROBE_MARKER))["keys"]
    )
    loader.read_flash.assert_called_once_with(NVS_OFFSET, NVS_SIZE)


def test_provisioned_device_cannot_produce_a_backup() -> None:
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        result, actions, errors = run_backup_main(
            root, probe_device_credentials=Mock(return_value=["refresh_tok", "wifi_pass"])
        )
        assert result == 1
        actions["read_full_flash"].assert_not_called()
        assert "holds Bop credentials" in errors
        assert not (root / "backups").exists()


def test_unprovisioned_device_produces_a_verified_backup() -> None:
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        image_bytes = flash_image()

        def fake_read(_port: str, _esptool: str, temporary: Path) -> None:
            temporary.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_bytes(image_bytes)

        result, actions, _ = run_backup_main(root, read_full_flash=Mock(side_effect=fake_read))
        assert result == 0
        actions["probe_device_credentials"].assert_called_once_with(USB_PORT)
        image = root / "backups/factory.bin"
        checksum = root / "backups/factory.bin.sha256"
        assert image.stat().st_size == device.FLASH_SIZE_BYTES
        assert (
            checksum.read_text(encoding="ascii").split()[0]
            == hashlib.sha256(image_bytes).hexdigest()
        )
        assert not (root / "backups/factory.bin.partial").exists()


def test_credential_bearing_read_never_becomes_a_backup() -> None:
    """The probe cannot be the only gate: the read itself is scanned too."""
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        leaked = flash_image(("wifi_pass",))

        def fake_read(_port: str, _esptool: str, temporary: Path) -> None:
            temporary.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_bytes(leaked)

        result, _, errors = run_backup_main(root, read_full_flash=Mock(side_effect=fake_read))
        assert result == 1
        assert "holds Bop credentials" in errors
        assert not (root / "backups/factory.bin").exists()
        assert not (root / "backups/factory.bin.partial").exists()


def test_provisioned_image_is_refused_even_when_size_and_digest_match() -> None:
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        backups = root / "backups"
        backups.mkdir()
        image, checksum = write_backup(backups, flash_image(("client_id",)))
        assert image.stat().st_size == device.FLASH_SIZE_BYTES
        assert device.sha256(image) == checksum.read_text(encoding="ascii").split()[0]

        result, actions, errors = run_backup_main(root)
    assert result == 1
    assert "holds Bop credentials" in errors
    actions["read_full_flash"].assert_not_called()
    actions["detect_usb_port"].assert_not_called()


def test_valid_backup_short_circuits_before_any_device_read() -> None:
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        backups = root / "backups"
        backups.mkdir()
        write_backup(backups, flash_image())
        result, actions, _ = run_backup_main(root)
    assert result == 0
    actions["detect_usb_port"].assert_not_called()
    actions["probe_device_credentials"].assert_not_called()
    actions["read_full_flash"].assert_not_called()


def test_image_scan_covers_every_credential_key() -> None:
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        for key in device.CREDENTIAL_KEYS:
            image = root / f"{key}.bin"
            image.write_bytes(flash_image((key,)))
            assert device.credential_keys_in_image(image) == [key]
        clean = root / "clean.bin"
        clean.write_bytes(flash_image())
        assert device.credential_keys_in_image(clean) == []


def test_image_scan_reads_only_the_nvs_region() -> None:
    """A key outside the NVS partition is not a Bop credential and must not trip the scan."""
    with tempfile.TemporaryDirectory() as name:
        image = Path(name) / "outside.bin"
        body = bytearray(flash_image())
        body[NVS_OFFSET + NVS_SIZE : NVS_OFFSET + NVS_SIZE + 9] = b"wifi_pass"
        image.write_bytes(bytes(body))
        assert device.credential_keys_in_image(image) == []


def test_short_image_is_refused_rather_than_read_as_clean() -> None:
    with tempfile.TemporaryDirectory() as name:
        image = Path(name) / "short.bin"
        image.write_bytes(b"\xff" * (NVS_OFFSET + NVS_SIZE - 1))
        try:
            device.credential_keys_in_image(image)
        except RuntimeError as error:
            assert "too small" in str(error)
        else:
            raise AssertionError("a truncated image passed the credential scan")


def test_restore_refuses_a_provisioned_image() -> None:
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        (root / "backups").mkdir()
        write_backup(root / "backups", flash_image(("refresh_tok",)))
        result, actions = run_restore_main(root, [])
    assert result == 1
    actions["write_verified_flash"].assert_not_called()


def test_restore_refuses_a_changed_image() -> None:
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        (root / "backups").mkdir()
        write_backup(root / "backups", flash_image(), digest="0" * 64)
        result, actions = run_restore_main(root, [RESTORE_APPROVAL])
    assert result == 1
    actions["write_verified_flash"].assert_not_called()


def test_restore_refuses_a_truncated_image() -> None:
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        (root / "backups").mkdir()
        write_backup(root / "backups", flash_image(size=NVS_OFFSET + NVS_SIZE))
        result, actions = run_restore_main(root, [RESTORE_APPROVAL])
    assert result == 1
    actions["write_verified_flash"].assert_not_called()


def test_restore_refuses_a_missing_image() -> None:
    with tempfile.TemporaryDirectory() as name:
        result, actions = run_restore_main(Path(name), [])
    assert result == 1
    actions["write_verified_flash"].assert_not_called()


def test_restore_approval_is_bound_to_the_device_mac() -> None:
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        (root / "backups").mkdir()
        write_backup(root / "backups", flash_image())
        result, actions = run_restore_main(root, ["RESTORE 00:00:00:00:00:00"])
    assert result == 1
    actions["write_verified_flash"].assert_not_called()


def test_restore_writes_only_after_approval() -> None:
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        (root / "backups").mkdir()
        image, _ = write_backup(root / "backups", flash_image())
        result, actions = run_restore_main(root, [RESTORE_APPROVAL])
    assert result == 0
    digest = hashlib.sha256(flash_image()).hexdigest()
    actions["write_verified_flash"].assert_called_once()
    called_identity, called_image, called_digest, started = actions[
        "write_verified_flash"
    ].call_args.args
    assert (called_identity, called_image, called_digest) == (IDENTITY, image, digest)
    assert started.name == "write-started"


def test_backup_reads_the_whole_flash_from_offset_zero() -> None:
    runner = Mock()
    with patch.object(backup_flash.subprocess, "run", runner):
        backup_flash.read_full_flash(USB_PORT, "/usr/bin/esptool", Path("/tmp/out.bin"))
    argv = runner.call_args.args[0]
    assert argv[:2] == ["/usr/bin/esptool", "--chip"]
    assert argv[2] == "esp32s3"
    assert argv[argv.index("read-flash") + 1 : argv.index("read-flash") + 3] == ["0x0", "0x1000000"]
    assert runner.call_args.kwargs["check"] is True


def test_short_flash_read_never_becomes_a_backup() -> None:
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)

        def fake_read(_port: str, _esptool: str, temporary: Path) -> None:
            temporary.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_bytes(flash_image(size=15 * 1024 * 1024))

        result, _, errors = run_backup_main(root, read_full_flash=Mock(side_effect=fake_read))
        assert result == 1
        assert str(device.FLASH_SIZE_BYTES) in errors
        assert str(15 * 1024 * 1024) in errors
        assert not (root / "backups/factory.bin").exists()
        assert not (root / "backups/factory.bin.partial").exists()


def test_backup_refuses_a_checksum_that_names_another_file() -> None:
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        backups = root / "backups"
        backups.mkdir()
        image_bytes = flash_image()
        image = backups / "factory.bin"
        image.write_bytes(image_bytes)
        digest = hashlib.sha256(image_bytes).hexdigest()
        (backups / "factory.bin.sha256").write_text(f"{digest}  other.bin\n", encoding="ascii")

        result, actions, _ = run_backup_main(root)
        assert result == 1
        actions["read_full_flash"].assert_not_called()


def test_image_scan_runs_before_the_digest() -> None:
    """A provisioned image must be refused without ever being hashed."""
    with tempfile.TemporaryDirectory() as name:
        backups = Path(name)
        image, checksum = write_backup(backups, flash_image(("wifi_ssid",)))
        hasher = Mock(side_effect=AssertionError("the image was hashed before the credential scan"))
        with patch.object(device, "sha256", hasher):
            try:
                device.verify_backup_image(image, checksum)
            except RuntimeError as error:
                assert "holds Bop credentials" in str(error)
            else:
                raise AssertionError("a provisioned image passed verification")
        hasher.assert_not_called()


def test_probe_fails_closed_on_a_bad_exit_code() -> None:
    runner = Mock(return_value=subprocess_result(1, "", "esptool exploded"))
    with patch.object(device, "run_esptool_python", runner):
        try:
            device.probe_device_credentials(USB_PORT)
        except RuntimeError as error:
            assert "esptool exploded" in str(error)
        else:
            raise AssertionError("the probe accepted a failed subprocess")


def test_probe_fails_closed_on_unexpected_output() -> None:
    payloads = [
        "no marker at all\n",
        f'{device.NVS_PROBE_MARKER}{{"keys": "wifi_pass"}}\n',
        f'{device.NVS_PROBE_MARKER}{{"keys": ["made_up_key"]}}\n',
        f"{device.NVS_PROBE_MARKER}[]\n",
        f'{device.NVS_PROBE_MARKER}{{"other": []}}\n',
    ]
    for payload in payloads:
        runner = Mock(return_value=subprocess_result(0, payload))
        with patch.object(device, "run_esptool_python", runner):
            try:
                device.probe_device_credentials(USB_PORT)
            except RuntimeError:
                pass
            else:
                raise AssertionError(f"the probe accepted {payload!r}")


def test_image_scan_clears_the_buffer_it_read() -> None:
    """The scan must not leave NVS bytes in a buffer after it returns."""
    captured: list[bytearray] = []
    original = device.clear_buffer

    def record(buffer: bytearray) -> None:
        captured.append(buffer)
        original(buffer)

    with tempfile.TemporaryDirectory() as name:
        image = Path(name) / "image.bin"
        image.write_bytes(flash_image(("wifi_pass",)))
        with patch.object(device, "clear_buffer", record):
            assert device.credential_keys_in_image(image) == ["wifi_pass"]
    assert captured, "credential_keys_in_image did not clear its buffer"
    assert all(byte == 0 for buffer in captured for byte in buffer)


def test_write_carries_the_approved_device_image_and_digest() -> None:
    """What the parent hands the write command. The gates themselves are executed below."""
    runner = Mock(return_value=subprocess_result(0))
    with patch.object(device, "run_esptool_python", runner):
        device.write_verified_flash(
            IDENTITY, Path("/tmp/factory.bin"), "abc123", Path("/tmp/started")
        )
    _, arguments = runner.call_args.args
    assert arguments == [
        USB_PORT,
        MAC,
        "/tmp/factory.bin",
        "abc123",
        str(device.FLASH_SIZE_BYTES),
        "/tmp/started",
        "0x9000",
        "0x6000",
        *device.CREDENTIAL_KEYS,
    ]
    # A 16 MB write must stay visible, or its silence reads as a hang.
    assert runner.call_args.kwargs["capture"] is False


def run_write_command(
    image: Path, digest: str, expected_mac: str = MAC, chip: str = "ESP32-S3", mac: str = MAC
) -> Mock:
    """Execute the real write command against a fake esptool. Returns the `main` mock."""
    esptool = ModuleType("esptool")
    loader = Mock()
    loader.CHIP_NAME = chip
    loader.read_mac = Mock(return_value=tuple(int(part, 16) for part in mac.split(":")))
    loader.run_stub = Mock(side_effect=AssertionError("the write must hand esptool the ROM loader"))
    main = Mock()
    setattr(esptool, "detect_chip", Mock(return_value=loader))
    setattr(esptool, "main", main)
    started = image.parent / "write-started"
    arguments = [
        "write",
        USB_PORT,
        expected_mac,
        str(image),
        digest,
        str(device.FLASH_SIZE_BYTES),
        str(started),
        "0x9000",
        "0x6000",
        *device.CREDENTIAL_KEYS,
    ]
    with (
        patch.dict(sys.modules, {"esptool": esptool}),
        patch.object(sys, "argv", arguments),
        redirect_stdout(StringIO()),
    ):
        exec(device.WRITE_VERIFIED_FLASH_COMMAND, {"__name__": "__write_test__"})
    main.started = started.exists()
    return main


def test_write_command_writes_a_clean_image_from_offset_zero() -> None:
    with tempfile.TemporaryDirectory() as name:
        image = Path(name) / "factory.bin"
        body = flash_image()
        image.write_bytes(body)
        main = run_write_command(image, hashlib.sha256(body).hexdigest())
    main.assert_called_once()
    assert main.started, "the write command must mark that the write started"
    argv = main.call_args.args[0]
    assert argv[argv.index("write-flash") + 1 :][-2:] == ["0x0", str(image)]
    for option in ("--flash-mode", "--flash-freq", "--flash-size"):
        assert argv[argv.index(option) + 1] == "keep", option
    assert argv[argv.index("--after") + 1] == "hard-reset"
    # esptool re-runs the stub itself, so it must be handed the ROM loader.
    assert main.call_args.kwargs["esp"].run_stub.call_count == 0


def test_write_command_refuses_every_post_approval_change() -> None:
    body = flash_image()
    digest = hashlib.sha256(body).hexdigest()
    cases = {
        # The digest here is the CORRECT one for the short content, so only the
        # size check can refuse it.
        "size": dict(content=flash_image(size=15 * 1024 * 1024), digest=None),
        "digest": dict(content=body, digest="0" * 64),
        "credentials": dict(content=flash_image(("wifi_pass",)), digest=None),
        "mac": dict(content=body, digest=digest, expected_mac="00:00:00:00:00:00"),
        "chip": dict(content=body, digest=digest, chip="ESP32-C3"),
    }
    for label, case in cases.items():
        content = case["content"]
        with tempfile.TemporaryDirectory() as name:
            image = Path(name) / "factory.bin"
            image.write_bytes(content)
            expected_digest = case["digest"] or hashlib.sha256(content).hexdigest()
            try:
                run_write_command(
                    image,
                    expected_digest,
                    expected_mac=case.get("expected_mac", MAC),
                    chip=case.get("chip", "ESP32-S3"),
                )
            except SystemExit:
                assert not (image.parent / "write-started").exists(), (
                    f"a refused {label} still marked the write as started"
                )
                continue
        raise AssertionError(f"the write command accepted a changed {label}")


def test_restore_write_failure_is_reported() -> None:
    """The write does not capture output, so its own message went to the terminal."""
    runner = Mock(return_value=subprocess_result(1))
    with patch.object(device, "run_esptool_python", runner):
        try:
            device.write_verified_flash(
                IDENTITY, Path("/tmp/factory.bin"), "abc123", Path("/tmp/started")
            )
        except RuntimeError as error:
            assert "write failed" in str(error)
        else:
            raise AssertionError("a failed write was reported as success")


def run_restore_to_interrupt(root: Path, started_write: bool) -> str:
    """Drive restore to a Ctrl+C, with the write either started or not yet started."""

    def interrupt(_identity, _image, _digest, started: Path) -> None:
        if started_write:
            started.touch()
        raise KeyboardInterrupt

    errors = StringIO()
    with (
        patch("builtins.input", side_effect=lambda _prompt: RESTORE_APPROVAL),
        patch.multiple(
            restore,
            project_root=Mock(return_value=root),
            detect_usb_port=Mock(return_value=USB_PORT),
            read_device_identity=Mock(return_value=IDENTITY),
            write_verified_flash=Mock(side_effect=interrupt),
        ),
        redirect_stdout(StringIO()),
        redirect_stderr(errors),
    ):
        try:
            restore.main()
        except SystemExit as exit_code:
            assert exit_code.code == 130
        except KeyboardInterrupt:
            assert not started_write, "an interrupted write escaped as a bare Ctrl+C"
        else:
            raise AssertionError("an interrupted restore was reported as success")
    return errors.getvalue()


def test_interrupt_after_the_write_starts_says_the_flash_is_incomplete() -> None:
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        (root / "backups").mkdir()
        write_backup(root / "backups", flash_image())
        errors = run_restore_to_interrupt(root, started_write=True)
    assert "incomplete" in errors
    assert "run `mise run restore` again" in errors.lower()


def test_interrupt_before_the_write_starts_claims_no_damage() -> None:
    """esptool spends seconds connecting before it writes. A Ctrl+C there broke nothing."""
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        (root / "backups").mkdir()
        write_backup(root / "backups", flash_image())
        errors = run_restore_to_interrupt(root, started_write=False)
    assert "incomplete" not in errors


def test_a_write_that_fails_part_way_warns_about_the_flash() -> None:
    """A dropped cable mid-write is the likelier route to a half-written flash."""

    def fail_after_starting(_identity, _image, _digest, started: Path) -> None:
        started.touch()
        raise RuntimeError("The device write failed. Read the esptool output above.")

    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        (root / "backups").mkdir()
        write_backup(root / "backups", flash_image())
        errors = StringIO()
        with (
            patch("builtins.input", side_effect=lambda _prompt: RESTORE_APPROVAL),
            patch.multiple(
                restore,
                project_root=Mock(return_value=root),
                detect_usb_port=Mock(return_value=USB_PORT),
                read_device_identity=Mock(return_value=IDENTITY),
                write_verified_flash=Mock(side_effect=fail_after_starting),
            ),
            redirect_stdout(StringIO()),
            redirect_stderr(errors),
        ):
            # 2, not 1: a possibly-incomplete flash must be distinguishable.
            assert restore.main() == 2
    assert "incomplete" in errors.getvalue()


def test_a_write_refused_before_it_starts_claims_no_damage() -> None:
    def refuse(_identity, _image, _digest, _started: Path) -> None:
        raise RuntimeError("The device changed after approval.")

    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        (root / "backups").mkdir()
        write_backup(root / "backups", flash_image())
        errors = StringIO()
        with (
            patch("builtins.input", side_effect=lambda _prompt: RESTORE_APPROVAL),
            patch.multiple(
                restore,
                project_root=Mock(return_value=root),
                detect_usb_port=Mock(return_value=USB_PORT),
                read_device_identity=Mock(return_value=IDENTITY),
                write_verified_flash=Mock(side_effect=refuse),
            ),
            redirect_stdout(StringIO()),
            redirect_stderr(errors),
        ):
            assert restore.main() == 1
    assert "incomplete" not in errors.getvalue()


def test_interrupt_at_the_prompt_says_nothing_was_changed() -> None:
    """Ctrl+C before the write must not read like Ctrl+C during it."""
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        (root / "backups").mkdir()
        write_backup(root / "backups", flash_image())
        with (
            patch("builtins.input", side_effect=KeyboardInterrupt),
            patch.multiple(
                restore,
                project_root=Mock(return_value=root),
                detect_usb_port=Mock(return_value=USB_PORT),
                read_device_identity=Mock(return_value=IDENTITY),
                write_verified_flash=Mock(),
            ),
            redirect_stdout(StringIO()),
            redirect_stderr(StringIO()),
        ):
            try:
                restore.main()
            except KeyboardInterrupt:
                pass
            else:
                raise AssertionError("restore swallowed the interrupt at the approval prompt")
            restore.write_verified_flash.assert_not_called()


def test_no_override_permits_a_provisioned_backup_or_restore() -> None:
    """The safety rule is that nothing a user types can turn these refusals off.

    This reads the parsed code, not the file text, so a comment that names an
    environment variable does not trip it and cannot hide one either. It covers
    the shapes an override would plausibly take: a command-line flag, an
    `argparse` parser, an environment read, and an interactive prompt that is not
    one of the two approvals. It does not prove the absence of every conceivable
    bypass.

    The embedded esptool commands are string constants here, so their own
    `sys.argv` unpacking is not code this module runs. That is how the offsets,
    digests, and key names reach those subprocesses.

    `tools/flash.py` does take a `--force` flag. It is outside this tuple on
    purpose: it can skip TAKING a backup, and the checks below it prove it
    never reaches these three modules to permit one.
    """
    root = backup_flash.project_root()
    banned_names = {"argparse", "getenv", "environ", "argv"}
    banned_flags = ("--force", "--yes", "--no-verify")

    for name in ("backup_flash.py", "restore.py", "device.py"):
        tree = ast.parse((root / "tools" / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                assert node.attr not in banned_names, f"{name} reaches {node.attr}"
            if isinstance(node, ast.Name):
                assert node.id not in banned_names, f"{name} reaches {node.id}"
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    assert alias.name not in banned_names, f"{name} imports {alias.name}"
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for flag in banned_flags:
                    assert flag not in node.value, f"{name} defines {flag}"

    # One prompt, the restore approval. It gates an action rather than turning a
    # refusal off. `deprovision.py` has its own two approvals and is outside this
    # tuple's scope.
    prompts = []
    for name in ("backup_flash.py", "restore.py", "device.py"):
        tree = ast.parse((root / "tools" / name).read_text(encoding="utf-8"))
        prompts += [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "input"
        ]
    assert len(prompts) == 1, (
        "backup, restore, and device take exactly one approval prompt between them. "
        "A second prompt is how an override would arrive."
    )

    # `flash.py` is the fourth module and its own rule. It may read argv and
    # define `--force`, because skipping a backup is what that flag does. What
    # it may not do is reach past `backup_flash.main` into the machinery the
    # refusal guards: a later flag that called `read_full_flash` or
    # `probe_device_credentials` from here would copy a provisioned board's
    # credentials to this computer with no check going red.
    permitted = {"backup_flash": {"main"}, "device": {"project_root"}, "run_idf": {"main"}}
    tree = ast.parse((root / "tools" / "flash.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            allowed = permitted.get(node.value.id)
            if allowed is not None:
                assert node.attr in allowed, f"flash.py reaches {node.value.id}.{node.attr}"
        if isinstance(node, ast.ImportFrom) and node.module in permitted:
            for alias in node.names:
                assert alias.name in permitted[node.module], (
                    f"flash.py imports {node.module}.{alias.name}"
                )


def run_flash_main(
    arguments: list[str], backup_code: int = 0, idf_code: int = 0
) -> tuple[int, Mock, Mock, str]:
    """Run the flash entry point with the backup step and idf.py replaced."""
    backup = Mock(return_value=backup_code)
    idf = Mock(return_value=idf_code)
    errors = StringIO()
    with (
        patch.object(flash.backup_flash, "main", backup),
        patch.object(flash.run_idf, "main", idf),
        patch.object(sys, "argv", ["flash.py", *arguments]),
        redirect_stdout(StringIO()),
        redirect_stderr(errors),
    ):
        result = flash.main()
    return result, backup, idf, errors.getvalue()


def test_unforced_flash_takes_the_backup_first() -> None:
    result, backup, idf, errors = run_flash_main([])
    assert result == 0
    backup.assert_called_once_with()
    idf.assert_called_once_with(["flash"])
    assert errors == ""


def test_a_failed_flash_reports_the_code_idf_returned() -> None:
    """The exit code of `idf.py` is the exit code of the task.

    Replacing the final `return run_idf.main(arguments)` with a bare call and
    `return 0` was the one mutation of nine that survived the whole suite: every
    other check asserted that idf.py was called, none that its answer was kept.
    A flash that failed would then report success to mise and to CI.
    """
    for code in (1, 2, 130):
        result, _, idf, _ = run_flash_main(["--force"], idf_code=code)
        assert result == code
        idf.assert_called_once_with(["flash"])


def test_a_refused_backup_still_stops_the_flash() -> None:
    """Without `--force` the gate keeps the exit code the backup chose."""
    result, backup, idf, _ = run_flash_main([], backup_code=1)
    assert result == 1
    backup.assert_called_once_with()
    idf.assert_not_called()


def test_forced_flash_skips_the_backup_and_warns() -> None:
    result, backup, idf, errors = run_flash_main(["--force"])
    assert result == 0
    backup.assert_not_called()
    idf.assert_called_once_with(["flash"])
    assert len(errors.strip().splitlines()) == 1
    assert "--force" in errors
    assert "backup" in errors


def test_the_flag_does_not_reach_idf_and_the_perf_arguments_do() -> None:
    """`--build-dir` becomes the `-B` that `flash-perf` used to spell itself."""
    for arguments in (
        ["--build-dir", "build-perf", "--force"],
        ["--force", "--build-dir", "build-perf"],
    ):
        result, backup, idf, _ = run_flash_main(arguments)
        assert result == 0, arguments
        backup.assert_not_called()
        idf.assert_called_once_with(["-B", "build-perf", "flash"])

    result, backup, idf, _ = run_flash_main(["--build-dir", "build-perf"])
    assert result == 0
    backup.assert_called_once_with()
    idf.assert_called_once_with(["-B", "build-perf", "flash"])


def test_other_arguments_keep_the_place_idf_wants_them_in() -> None:
    """idf.py takes flash options after the subcommand, where the old task put them."""
    for arguments, expected in (
        (["--trace"], ["flash", "--trace"]),
        (["--force", "--trace"], ["flash", "--trace"]),
        (
            ["--build-dir", "build-perf", "--force", "--trace"],
            ["-B", "build-perf", "flash", "--trace"],
        ),
        # Bop takes the first `--force`. The second is idf.py's own flash flag,
        # which would otherwise have no spelling left.
        (["--force", "--force"], ["flash", "--force"]),
    ):
        _, _, idf, _ = run_flash_main(arguments)
        idf.assert_called_once_with(expected)


def test_the_forced_warning_names_the_skipped_step_and_the_missing_image() -> None:
    with tempfile.TemporaryDirectory() as name:
        image = Path(name) / "factory.bin"
        missing = flash.forced_warning(image)
        assert len(missing.splitlines()) == 1
        assert "--force" in missing
        assert str(image) in missing
        assert "no factory image exists" in missing

        image.write_bytes(b"")
        present = flash.forced_warning(image)
        assert len(present.splitlines()) == 1
        assert "no factory image exists" not in present
        assert "not verified" in present


def test_a_forced_flash_never_enters_the_backup_tool() -> None:
    """`--force` skips taking a backup. It must not reach the port, the probe, or a read."""
    idf = Mock(return_value=0)
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        with (
            patch.multiple(
                backup_flash,
                esptool_path=Mock(side_effect=AssertionError("a forced flash called esptool")),
                detect_usb_port=Mock(side_effect=AssertionError("a forced flash opened the port")),
                probe_device_credentials=Mock(
                    side_effect=AssertionError("a forced flash probed the device")
                ),
                read_full_flash=Mock(side_effect=AssertionError("a forced flash read the flash")),
            ),
            # `flash.py` imports `project_root` from `device`, so patching the
            # name on `backup_flash` left the warning pointing at the real
            # repository and the assertion below reading the real backups
            # directory.
            patch.object(flash, "project_root", Mock(return_value=root)),
            patch.object(flash.run_idf, "main", idf),
            patch.object(sys, "argv", ["flash.py", "--force"]),
            redirect_stdout(StringIO()),
            redirect_stderr(StringIO()),
        ):
            assert flash.main() == 0
        assert not (root / "backups").exists()
    idf.assert_called_once_with(["flash"])


def test_no_flag_makes_a_provisioned_device_produce_a_backup() -> None:
    """The refusal is absolute. `--force` skips a backup; it never permits one.

    Driven through `flash.main`, the entry point a user actually reaches. An
    earlier version of this check looped over four argument lists while patching
    `sys.argv` for `backup_flash`, which reads no argv and was called directly:
    the four runs were one run repeated, and the path from the command line to
    the refusal was never walked.
    """
    for arguments in ([], ["--yes"], ["--build-dir", "build-perf"]):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            idf = Mock(return_value=0)
            errors = StringIO()
            actions = {
                "project_root": Mock(return_value=root),
                "esptool_path": Mock(return_value="esptool"),
                "detect_usb_port": Mock(return_value="/dev/null"),
                "probe_device_credentials": Mock(return_value=["refresh_tok", "wifi_pass"]),
                "read_full_flash": Mock(),
            }
            with (
                patch.multiple(backup_flash, **actions),
                patch.object(flash.run_idf, "main", idf),
                patch.object(sys, "argv", ["flash.py", *arguments]),
                redirect_stdout(StringIO()),
                redirect_stderr(errors),
            ):
                result = flash.main()
            # The refusal text and the exit code both reach the caller, and the
            # flash never runs.
            assert result == 1, arguments
            actions["read_full_flash"].assert_not_called()
            assert "holds Bop credentials" in errors.getvalue(), arguments
            idf.assert_not_called()
            assert not (root / "backups").exists()


def test_the_flash_tasks_reach_the_gate_and_provision_still_depends_on_backup() -> None:
    """The gate left `mise.toml`, so the tasks must reach it through `tools/flash.py`."""
    with (device.project_root() / "mise.toml").open("rb") as source:
        tasks = tomllib.load(source)["tasks"]

    for name in ("flash", "flash-perf"):
        assert "backup" not in tasks[name].get("depends", []), name
        assert "tools/flash.py" in tasks[name]["run"], name
        assert "run_idf.py" not in tasks[name]["run"], name
        # A task that forces itself would skip the backup for every user, with
        # nothing on the command line to show for it.
        assert flash.FORCE_FLAG not in tasks[name]["run"], name

    # `flash-perf` names its build directory through the flag `flash.py` owns,
    # so the `-B` still reaches idf.py before the subcommand.
    assert f"{flash.BUILD_DIR_FLAG} build-perf" in tasks["flash-perf"]["run"]

    # Provisioning is the last moment a credential-free factory image can be
    # taken, so its backup dependency stays where it is.
    assert "backup" in tasks["provision"]["depends"]
    assert tasks["backup"]["run"] == "python tools/backup_flash.py"


def main() -> int:
    test_probe_reads_only_the_nvs_region()
    test_probe_reports_each_credential_key()
    test_provisioned_device_cannot_produce_a_backup()
    test_unprovisioned_device_produces_a_verified_backup()
    test_credential_bearing_read_never_becomes_a_backup()
    test_provisioned_image_is_refused_even_when_size_and_digest_match()
    test_valid_backup_short_circuits_before_any_device_read()
    test_image_scan_covers_every_credential_key()
    test_image_scan_reads_only_the_nvs_region()
    test_short_image_is_refused_rather_than_read_as_clean()
    test_restore_refuses_a_provisioned_image()
    test_restore_refuses_a_changed_image()
    test_restore_refuses_a_truncated_image()
    test_restore_refuses_a_missing_image()
    test_restore_approval_is_bound_to_the_device_mac()
    test_restore_writes_only_after_approval()
    test_backup_reads_the_whole_flash_from_offset_zero()
    test_short_flash_read_never_becomes_a_backup()
    test_backup_refuses_a_checksum_that_names_another_file()
    test_image_scan_runs_before_the_digest()
    test_probe_fails_closed_on_a_bad_exit_code()
    test_probe_fails_closed_on_unexpected_output()
    test_image_scan_clears_the_buffer_it_read()
    test_write_carries_the_approved_device_image_and_digest()
    test_write_command_writes_a_clean_image_from_offset_zero()
    test_write_command_refuses_every_post_approval_change()
    test_restore_write_failure_is_reported()
    test_interrupt_after_the_write_starts_says_the_flash_is_incomplete()
    test_interrupt_before_the_write_starts_claims_no_damage()
    test_a_write_that_fails_part_way_warns_about_the_flash()
    test_a_write_refused_before_it_starts_claims_no_damage()
    test_interrupt_at_the_prompt_says_nothing_was_changed()
    test_no_override_permits_a_provisioned_backup_or_restore()
    test_unforced_flash_takes_the_backup_first()
    test_a_failed_flash_reports_the_code_idf_returned()
    test_a_refused_backup_still_stops_the_flash()
    test_forced_flash_skips_the_backup_and_warns()
    test_the_flag_does_not_reach_idf_and_the_perf_arguments_do()
    test_other_arguments_keep_the_place_idf_wants_them_in()
    test_the_forced_warning_names_the_skipped_step_and_the_missing_image()
    test_a_forced_flash_never_enters_the_backup_tool()
    test_no_flag_makes_a_provisioned_device_produce_a_backup()
    test_the_flash_tasks_reach_the_gate_and_provision_still_depends_on_backup()
    print("Backup and restore safety checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
