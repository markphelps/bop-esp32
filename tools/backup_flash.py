#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mark Phelps
# SPDX-License-Identifier: Apache-2.0

"""Read the complete factory flash once, before provisioning, and record its digest."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

from device import (
    FLASH_SIZE,
    FLASH_SIZE_BYTES,
    detect_usb_port,
    esptool_path,
    probe_device_credentials,
    project_root,
    require_credential_free_image,
    sha256,
    verify_backup_image,
)

PROVISIONED_DEVICE_MESSAGE = """\
This device holds Bop credentials, so a full-flash backup would copy your WiFi
password and your Spotify refresh token onto this computer as plaintext.

A factory backup is only useful from an unprovisioned device. Take the backup
before you provision.

To get an unprovisioned device again, run `mise run deprovision`.\
"""


def require_unprovisioned_device(port: str) -> None:
    found = probe_device_credentials(port)
    if found:
        raise RuntimeError(f"{PROVISIONED_DEVICE_MESSAGE}\n\nKeys found: {', '.join(found)}")


def read_full_flash(port: str, esptool: str, temporary: Path) -> None:
    subprocess.run(
        [esptool, "--chip", "esp32s3", "--port", port, "read-flash", "0x0", FLASH_SIZE, str(temporary)],
        check=True,
    )


def main() -> int:
    backup_dir = project_root() / "backups"
    image = backup_dir / "factory.bin"
    checksum = backup_dir / "factory.bin.sha256"

    try:
        esptool = esptool_path()
        if image.exists() or checksum.exists():
            verify_backup_image(image, checksum)
            print(f"A valid factory backup already exists at {image}. No file was overwritten.")
            return 0

        # Read only the 24 KB credential partition first. A provisioned device
        # must never reach the full-flash read below.
        port = detect_usb_port()
        require_unprovisioned_device(port)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"backup failed: {error}", file=sys.stderr)
        return 1

    backup_dir.mkdir(parents=True, exist_ok=True)
    temporary = backup_dir / "factory.bin.partial"
    temporary.unlink(missing_ok=True)
    try:
        read_full_flash(port, esptool, temporary)
        # Scan before the file takes its final name, so a credential-bearing read
        # is discarded with the temporary file and never becomes a backup.
        size = temporary.stat().st_size
        if size != FLASH_SIZE_BYTES:
            raise RuntimeError(f"The flash read produced {size} bytes, not {FLASH_SIZE_BYTES}")
        require_credential_free_image(temporary)
        temporary.replace(image)
        digest = sha256(image)
        checksum.write_text(f"{digest}  {image.name}\n", encoding="ascii")
    except KeyboardInterrupt:
        temporary.unlink(missing_ok=True)
        raise
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        temporary.unlink(missing_ok=True)
        print(f"backup failed: {error}", file=sys.stderr)
        return 1

    print(f"Saved {image} ({digest})")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Canceled.", file=sys.stderr)
        raise SystemExit(130)
