#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mark Phelps
# SPDX-License-Identifier: Apache-2.0

"""Write the verified credential-free factory image back to the device."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile

from device import (
    DeviceIdentity,
    detect_usb_port,
    project_root,
    read_device_identity,
    verify_backup_image,
    write_verified_flash,
)

WARNING = """\
A restore overwrites all 16 MB of flash. It replaces the firmware and every
value the device holds, and you cannot undo it.

Run `mise run deprovision` first, so that Spotify stops trusting this device.\
"""


def verified_backup() -> tuple[Path, str]:
    """Return the factory image and its digest, only when the image is safe to write."""
    backup_dir = project_root() / "backups"
    image = backup_dir / "factory.bin"
    checksum = backup_dir / "factory.bin.sha256"
    if not image.is_file() and not checksum.is_file():
        raise RuntimeError(f"No factory backup was found at {image}. There is nothing to restore.")
    return image, verify_backup_image(image, checksum)


def require_restore_approval(identity: DeviceIdentity, image: Path) -> None:
    print(WARNING)
    print()
    print(f"Image:  {image}")
    print(f"Device: port {identity.port}, MAC {identity.mac}")
    approval = f"RESTORE {identity.mac}"
    if input(f"Type {approval} to overwrite this device's flash: ") != approval:
        raise RuntimeError("Restore stopped. The device flash was not changed.")


def main() -> int:
    with tempfile.TemporaryDirectory() as workspace:
        # The write command creates this file at the moment the first byte
        # reaches the flash. Its absence means the flash was never touched, so
        # the two outcomes never get the same message.
        started = Path(workspace) / "write-started"
        try:
            image, digest = verified_backup()
            port = detect_usb_port()
            identity = read_device_identity(port)
            require_restore_approval(identity, image)
            # The image and the device are checked again inside this call,
            # because both can change while the prompt above waits.
            write_verified_flash(identity, image, digest, started)
            print(f"Restored {image} to the device at {identity.port}.")
        except KeyboardInterrupt:
            if not started.exists():
                # Nothing was touched. Let the module-level handler say so.
                raise
            warn_incomplete_flash("Canceled during the flash write.")
            raise SystemExit(130)
        except (
            EOFError,
            OSError,
            RuntimeError,
            subprocess.CalledProcessError,
            ValueError,
        ) as error:
            print(f"restore failed: {error}", file=sys.stderr)
            if started.exists():
                # Exit 2 separates "the flash may be incomplete" from every
                # other failure, which all leave the device untouched and
                # exit 1. A disposal script can act on the difference.
                warn_incomplete_flash("The write stopped part way through.")
                return 2
            return 1
    return 0


def warn_incomplete_flash(reason: str) -> None:
    print(
        f"{reason} The device flash is now incomplete.\n"
        "Do not use the board yet. Run `mise run restore` again.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Canceled. The device flash was not changed.", file=sys.stderr)
        raise SystemExit(130)
