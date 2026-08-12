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
from types import ModuleType
from unittest.mock import Mock, patch

import backup_flash
import device
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
    body = bytearray(b"\xFF" * size)
    position = NVS_OFFSET
    for key in keys:
        encoded = key.encode("ascii")
        body[position : position + len(encoded)] = encoded
        position += len(encoded) + 8
    return bytes(body)


def write_backup(directory: Path, image_bytes: bytes, digest: str | None = None) -> tuple[Path, Path]:
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


def run_restore_main(root: Path, inputs: list[str], **replacements: Mock) -> tuple[int, dict[str, Mock]]:
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
    loader.read_flash = Mock(return_value=flash_image(device.CREDENTIAL_KEYS)[NVS_OFFSET : NVS_OFFSET + NVS_SIZE])
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
        assert checksum.read_text(encoding="ascii").split()[0] == hashlib.sha256(image_bytes).hexdigest()
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
        image.write_bytes(b"\xFF" * (NVS_OFFSET + NVS_SIZE - 1))
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
    called_identity, called_image, called_digest, started = actions["write_verified_flash"].call_args.args
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
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "input"
        ]
    assert len(prompts) == 1, (
        "backup, restore, and device take exactly one approval prompt between them. "
        "A second prompt is how an override would arrive."
    )


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
    print("Backup and restore safety checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
