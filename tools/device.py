#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mark Phelps
# SPDX-License-Identifier: Apache-2.0

"""Shared device constants, credential scanning, and esptool process helpers."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

from detect_port import esptool_python

PARTITION_TABLE_OFFSET = "0x8000"
PARTITION_TABLE_SIZE = "0x1000"
NVS_OFFSET = "0x9000"
NVS_SIZE = "0x6000"
APP_OFFSET = "0x10000"
FLASH_SIZE = "0x1000000"
FLASH_SIZE_BYTES = 16 * 1024 * 1024

CREDENTIAL_KEYS = ("wifi_ssid", "wifi_pass", "client_id", "refresh_tok")

IDENTITY_MARKER = "BOP_DEVICE_IDENTITY="
NVS_PROBE_MARKER = "BOP_NVS_PROBE="

# Reads only the NVS region, never the whole flash. It prints the names of the
# credential keys it finds and never a value. The names are public constants.
PROBE_NVS_COMMAND = r"""
import json
import sys
import esptool

port, nvs_offset, nvs_size, *keys = sys.argv[1:]
nvs_offset = int(nvs_offset, 0)
nvs_size = int(nvs_size, 0)
loader = esptool.detect_chip(port=port).run_stub()
image = bytearray(loader.read_flash(nvs_offset, nvs_size))
try:
    if len(image) != nvs_size:
        raise SystemExit("The NVS probe read an unexpected number of bytes")
    found = sorted({key for key in keys if key.encode("ascii") in image})
finally:
    for position in range(len(image)):
        image[position] = 0
    del image
print("BOP_NVS_PROBE=" + json.dumps({"keys": found}, sort_keys=True))
"""

# Identifies the board without reading NVS and without needing Bop firmware, so
# that a restore can run against a device in any state.
READ_IDENTITY_COMMAND = r"""
import json
import sys
import esptool

port, = sys.argv[1:]
loader = esptool.detect_chip(port=port).run_stub()
if loader.CHIP_NAME != "ESP32-S3":
    raise SystemExit(f"Expected an ESP32-S3, found {loader.CHIP_NAME}")
mac = ":".join(f"{byte:02x}" for byte in loader.read_mac("BASE_MAC"))
print("BOP_DEVICE_IDENTITY=" + json.dumps({"mac": mac, "port": port}, sort_keys=True))
"""


# Re-checks the image and the device after the user approves, then writes on the
# same connection. Nothing between the approval and the write is trusted: a file
# swapped while the prompt waited, or a board swapped on the same port, stops
# here. `esptool.main` takes the already-open loader, so no second chip
# detection can land on a different device.
#
# Hand `esptool.main` the ROM loader, never a stub-running one. `prepare_esp_object`
# runs the stub itself, and `StubMixin.__init__` does not copy
# `sync_stub_detected`, so a loader that already ran the stub makes esptool load
# the stub on top of itself and fail with "Stub flasher is resident at ...".
# `CHIP_NAME` and `read_mac` both work on the ROM loader, so the two re-checks
# below do not need the stub.
#
# The flash options are all pinned to `keep` because esptool reads its defaults
# for them from `ESPTOOL_FF`, `ESPTOOL_FM`, `ESPTOOL_FS`, and `ESPTOOL_AFTER`. An
# environment variable must not change the bytes that reach a device after this
# command verified their digest.
WRITE_VERIFIED_FLASH_COMMAND = r"""
import hashlib
import os
import sys
import esptool

port, expected_mac, image_path, expected_digest, expected_size, started_path, nvs_offset, nvs_size, *keys = sys.argv[1:]
expected_size = int(expected_size)
nvs_offset = int(nvs_offset, 0)
nvs_size = int(nvs_size, 0)

size = os.path.getsize(image_path)
if size != expected_size:
    raise SystemExit(f"The image changed after approval. Expected {expected_size} bytes, found {size}")

digest = hashlib.sha256()
with open(image_path, "rb") as source:
    for block in iter(lambda: source.read(1024 * 1024), b""):
        digest.update(block)
if digest.hexdigest() != expected_digest:
    raise SystemExit("The image changed after approval. Its SHA-256 no longer matches")

region = bytearray(nvs_size)
try:
    with open(image_path, "rb") as source:
        source.seek(nvs_offset)
        if source.readinto(region) != nvs_size:
            raise SystemExit("The image ends inside the NVS partition")
    found = sorted({key for key in keys if key.encode("ascii") in region})
finally:
    for position in range(len(region)):
        region[position] = 0
    del region
if found:
    raise SystemExit("The image holds Bop credentials: " + ", ".join(found))

loader = esptool.detect_chip(port=port)
if loader.CHIP_NAME != "ESP32-S3":
    raise SystemExit(f"Expected an ESP32-S3, found {loader.CHIP_NAME}")
actual_mac = ":".join(f"{byte:02x}" for byte in loader.read_mac("BASE_MAC"))
if actual_mac != expected_mac:
    raise SystemExit(f"The device changed after approval. Expected {expected_mac}, found {actual_mac}")
# The last statement before the write command is entered. Every gate above has
# passed by now. `esptool.main` still uploads the stub and reads the flash size
# after this point, so a failure just after it may not have written a byte; the
# marker errs toward warning. The parent reads this file to tell "refused,
# nothing written" from "stopped part way through".
with open(started_path, "w"):
    pass
esptool.main(
    [
        "--chip", "esp32s3",
        "--port", port,
        "--after", "hard-reset",
        "write-flash",
        "--flash-mode", "keep",
        "--flash-freq", "keep",
        "--flash-size", "keep",
        "0x0", image_path,
    ],
    esp=loader,
)
"""


@dataclass(frozen=True)
class DeviceIdentity:
    port: str
    mac: str


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def detect_usb_port() -> str:
    result = subprocess.run(
        [sys.executable, str(project_root() / "tools/detect_port.py")],
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "USB port detection failed"
        raise RuntimeError(message)
    return result.stdout.strip()


def esptool_path() -> str:
    esptool = shutil.which("esptool") or shutil.which("esptool.py")
    if esptool is None:
        raise RuntimeError("esptool is unavailable. Run `mise install` first")
    return esptool


def require_esptool_python() -> Path:
    python = esptool_python()
    if python is None:
        raise RuntimeError("esptool Python is unavailable. Run `mise install` first")
    return python


def run_esptool_python(
    command: str, arguments: list[str], capture: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run an esptool command. Set `capture` to False for a long, visible operation."""
    return subprocess.run(
        [str(require_esptool_python()), "-c", command, *arguments],
        check=False,
        text=True,
        capture_output=capture,
    )


def command_error(result: subprocess.CompletedProcess[str], fallback: str) -> RuntimeError:
    message = result.stderr.strip() or result.stdout.strip() or fallback
    return RuntimeError(message)


def marker_payload(
    result: subprocess.CompletedProcess[str], marker: str, subject: str, noun: str = "result"
) -> dict:
    line = next((line for line in result.stdout.splitlines() if line.startswith(marker)), None)
    if line is None:
        raise RuntimeError(f"{subject} returned no {noun}")
    payload = json.loads(line.removeprefix(marker))
    if not isinstance(payload, dict):
        raise RuntimeError(f"{subject} returned invalid data")
    return payload


def clear_buffer(buffer: bytearray) -> None:
    """Overwrite a buffer that held NVS bytes, so the values do not linger."""
    for position in range(len(buffer)):
        buffer[position] = 0


def credential_keys_in(data: bytes | bytearray) -> list[str]:
    return sorted({key for key in CREDENTIAL_KEYS if key.encode("ascii") in data})


def credential_keys_in_image(image: Path) -> list[str]:
    """Scan only the NVS region of a flash image. It never reads the whole file."""
    offset = int(NVS_OFFSET, 0)
    size = int(NVS_SIZE, 0)
    if not image.is_file():
        raise RuntimeError(f"{image} is not a file")
    if image.stat().st_size < offset + size:
        raise RuntimeError(f"{image} is too small to hold a Bop NVS partition")
    buffer = bytearray(size)
    try:
        with image.open("rb") as source:
            source.seek(offset)
            read = source.readinto(buffer)
        if read != size:
            raise RuntimeError(f"{image} ended inside the NVS partition")
        return credential_keys_in(buffer)
    finally:
        clear_buffer(buffer)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_credential_free_image(image: Path) -> None:
    """Refuse an image whose NVS partition holds credentials, before anything hashes it."""
    found = credential_keys_in_image(image)
    if found:
        raise RuntimeError(
            f"{image} holds Bop credentials in its NVS partition, so it came from a "
            f"provisioned device. Bop will not hash, accept, or restore it. "
            f"Keys found: {', '.join(found)}. "
            f"Delete it yourself after you decide what to do with it. Do not commit "
            f"it, and do not copy it to another computer."
        )


def verify_backup_image(image: Path, checksum: Path) -> str:
    """Return the digest of a complete, unchanged, credential-free factory image.

    The credential scan runs before the digest, so a provisioned image is refused
    even when its size and its SHA-256 file agree.
    """
    remedy = "Move it out of the way, then try again."
    if not image.is_file() or not checksum.is_file():
        raise RuntimeError(f"{image} and its SHA-256 file must both exist. {remedy}")
    if image.stat().st_size != FLASH_SIZE_BYTES:
        raise RuntimeError(
            f"{image} is {image.stat().st_size} bytes, not a complete {FLASH_SIZE_BYTES}-byte "
            f"flash image. {remedy}"
        )
    require_credential_free_image(image)
    digest = sha256(image)
    fields = checksum.read_text(encoding="ascii").strip().split()
    if len(fields) != 2 or fields[0] != digest or fields[1] != image.name:
        raise RuntimeError(f"{image} does not match {checksum}. {remedy}")
    return digest


def probe_device_credentials(port: str) -> list[str]:
    """Read only the device NVS region and report which credential keys it holds."""
    result = run_esptool_python(PROBE_NVS_COMMAND, [port, NVS_OFFSET, NVS_SIZE, *CREDENTIAL_KEYS])
    if result.returncode != 0:
        raise command_error(result, "The NVS credential probe failed")
    payload = marker_payload(result, NVS_PROBE_MARKER, "The NVS credential probe")
    keys = payload.get("keys")
    if not isinstance(keys, list) or not all(key in CREDENTIAL_KEYS for key in keys):
        raise RuntimeError("The NVS credential probe returned invalid data")
    return sorted(keys)


def read_device_identity(port: str) -> DeviceIdentity:
    result = run_esptool_python(READ_IDENTITY_COMMAND, [port])
    if result.returncode != 0:
        raise command_error(result, "Device identity check failed")
    identity = marker_payload(result, IDENTITY_MARKER, "Device identity check", noun="identity")
    if identity.get("port") != port or not identity.get("mac"):
        raise RuntimeError("Device identity check returned invalid data")
    return DeviceIdentity(port=identity["port"], mac=identity["mac"])


def write_verified_flash(
    identity: DeviceIdentity, image: Path, digest: str, started_marker: Path
) -> None:
    """Re-check the image and the device after approval, then write on one connection.

    `started_marker` is a path the command creates at the moment the write starts.
    Everything before that point can still refuse, and leaves the flash untouched.
    """
    result = run_esptool_python(
        WRITE_VERIFIED_FLASH_COMMAND,
        [
            identity.port,
            identity.mac,
            str(image),
            digest,
            str(FLASH_SIZE_BYTES),
            str(started_marker),
            NVS_OFFSET,
            NVS_SIZE,
            *CREDENTIAL_KEYS,
        ],
        # A 16 MB write takes minutes. Let esptool's progress reach the terminal,
        # so that the silence is not read as a hang and interrupted.
        capture=False,
    )
    if result.returncode != 0:
        raise RuntimeError("The device write failed. Read the esptool output above.")
