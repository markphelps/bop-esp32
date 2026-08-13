#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mark Phelps
# SPDX-License-Identifier: Apache-2.0

"""Remove Spotify access, then erase Bop credentials through USB."""

from __future__ import annotations

import subprocess
import sys
import webbrowser

from device import (
    APP_OFFSET,
    CREDENTIAL_KEYS,
    IDENTITY_MARKER,
    NVS_OFFSET,
    NVS_SIZE,
    PARTITION_TABLE_OFFSET,
    PARTITION_TABLE_SIZE,
    DeviceIdentity,
    command_error,
    detect_usb_port,
    marker_payload,
    run_esptool_python,
)

CONNECTED_APPS_URL = "https://www.spotify.com/account/apps/"
DEVICE_VALIDATION_SOURCE = r"""
import hashlib
import struct

def inspect_bop_device(loader, table_offset, table_size, nvs_offset, nvs_size, app_offset):
    if loader.CHIP_NAME != "ESP32-S3":
        raise SystemExit(f"Expected an ESP32-S3, found {loader.CHIP_NAME}")
    table = loader.read_flash(table_offset, table_size)
    entries = {}
    digest = hashlib.md5()
    found_digest = False
    found_end = False
    for position in range(0, len(table), 32):
        entry = table[position : position + 32]
        if entry == b"\xFF" * 32:
            found_end = True
            break
        if entry[:2] == b"\xEB\xEB":
            if found_digest or entry[:16] != b"\xEB\xEB" + b"\xFF" * 14:
                raise SystemExit("The connected device has an invalid partition-table digest record")
            if entry[16:] != digest.digest():
                raise SystemExit("The connected device has a partition-table digest mismatch")
            found_digest = True
            continue
        if found_digest:
            raise SystemExit("The connected device has data after the partition-table digest")
        digest.update(entry)
        magic, entry_type, subtype, offset, size, label, flags = struct.unpack(
            "<HBBLL16sL", entry
        )
        if magic != 0x50AA:
            raise SystemExit("The connected device has an invalid partition table")
        name = label.split(b"\0", 1)[0].decode("ascii")
        entries[name] = (entry_type, subtype, offset, size, flags)
    if not found_digest or not found_end:
        raise SystemExit("The connected device has an incomplete partition table")
    expected_nvs = (1, 2, nvs_offset, nvs_size, 0)
    expected_factory = (0, 0, app_offset, 0x800000, 0)
    if entries.get("nvs") != expected_nvs or entries.get("factory") != expected_factory:
        raise SystemExit("The connected device does not have the Bop partition layout")
    description = loader.read_flash(app_offset + 0x20, 256)
    magic, = struct.unpack_from("<L", description)
    project_name = description[48:80].split(b"\0", 1)[0].decode("ascii")
    if magic != 0xABCD5432 or project_name != "bop":
        raise SystemExit("The connected device is not running Bop firmware")
    return ":".join(f"{byte:02x}" for byte in loader.read_mac("BASE_MAC"))
"""
INSPECT_DEVICE_COMMAND = (
    DEVICE_VALIDATION_SOURCE
    + r"""
import json
import sys
import esptool

port, table_offset, table_size, nvs_offset, nvs_size, app_offset = sys.argv[1:]
loader = esptool.detect_chip(port=port).run_stub()
mac = inspect_bop_device(
    loader,
    int(table_offset, 0),
    int(table_size, 0),
    int(nvs_offset, 0),
    int(nvs_size, 0),
    int(app_offset, 0),
)
print("BOP_DEVICE_IDENTITY=" + json.dumps({"mac": mac, "port": port}, sort_keys=True))
"""
)
ERASE_VERIFIED_DEVICE_COMMAND = (
    DEVICE_VALIDATION_SOURCE
    + r"""
import sys
import esptool

port, expected_mac, table_offset, table_size, nvs_offset, nvs_size, app_offset, *keys = sys.argv[1:]
table_offset = int(table_offset, 0)
table_size = int(table_size, 0)
nvs_offset = int(nvs_offset, 0)
nvs_size = int(nvs_size, 0)
app_offset = int(app_offset, 0)
loader = esptool.detect_chip(port=port).run_stub()
actual_mac = inspect_bop_device(
    loader, table_offset, table_size, nvs_offset, nvs_size, app_offset
)
if actual_mac != expected_mac:
    raise SystemExit(f"The device changed after approval. Expected {expected_mac}, found {actual_mac}")
loader.erase_region(nvs_offset, nvs_size)
image = loader.read_flash(nvs_offset, nvs_size)
if len(image) != nvs_size:
    raise SystemExit("NVS read-back has an unexpected size")
if any(key.encode("ascii") in image for key in keys):
    raise SystemExit("NVS read-back still contains a Bop credential key")
if any(byte != 0xFF for byte in image):
    raise SystemExit("NVS read-back contains data after erase")
loader.hard_reset()
"""
)


def inspect_device(port: str) -> DeviceIdentity:
    result = run_esptool_python(
        INSPECT_DEVICE_COMMAND,
        [
            port,
            PARTITION_TABLE_OFFSET,
            PARTITION_TABLE_SIZE,
            NVS_OFFSET,
            NVS_SIZE,
            APP_OFFSET,
        ],
    )
    if result.returncode != 0:
        raise command_error(result, "Device identity check failed")
    identity = marker_payload(result, IDENTITY_MARKER, "Device identity check", noun="identity")
    if identity.get("port") != port or not identity.get("mac"):
        raise RuntimeError("Device identity check returned invalid data")
    return DeviceIdentity(port=identity["port"], mac=identity["mac"])


def open_connected_apps() -> None:
    print(f"Spotify connected-apps page: {CONNECTED_APPS_URL}")
    print("Remove Bop access on that page. Then return to this terminal.")
    try:
        opened = webbrowser.open(CONNECTED_APPS_URL)
    except webbrowser.Error as error:
        message = f"The browser did not open. Open this URL manually: {CONNECTED_APPS_URL}"
        raise RuntimeError(message) from error
    if not opened:
        raise RuntimeError(
            f"The browser did not open. Open this URL manually: {CONNECTED_APPS_URL}"
        )


def require_account_removal() -> None:
    if input("Type REMOVED after you remove Bop access from Spotify: ") != "REMOVED":
        raise RuntimeError("Deprovisioning stopped. Spotify account removal was not confirmed.")


def require_erase_approval(identity: DeviceIdentity) -> None:
    print(f"Bop device: port {identity.port}, MAC {identity.mac}")
    approval = f"ERASE {identity.mac}"
    if input(f"Type {approval} to erase this device's credential partition: ") != approval:
        raise RuntimeError(
            "Deprovisioning stopped. The device credential partition was not erased."
        )


def erase_verified_device(identity: DeviceIdentity) -> None:
    result = run_esptool_python(
        ERASE_VERIFIED_DEVICE_COMMAND,
        [
            identity.port,
            identity.mac,
            PARTITION_TABLE_OFFSET,
            PARTITION_TABLE_SIZE,
            NVS_OFFSET,
            NVS_SIZE,
            APP_OFFSET,
            *CREDENTIAL_KEYS,
        ],
    )
    if result.returncode != 0:
        raise command_error(result, "Device erase or read-back failed")


def main() -> int:
    try:
        port = detect_usb_port()
        identity = inspect_device(port)
        open_connected_apps()
        require_account_removal()
        require_erase_approval(identity)
        erase_verified_device(identity)
        print("Bop access is removed. The device has restarted in provisioning mode.")
    except (EOFError, OSError, RuntimeError, subprocess.CalledProcessError, ValueError) as error:
        print(f"deprovisioning failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Canceled.", file=sys.stderr)
        raise SystemExit(130)
