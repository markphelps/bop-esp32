#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mark Phelps
# SPDX-License-Identifier: Apache-2.0

"""Find one connected Espressif USB serial port."""

from __future__ import annotations

import os
from pathlib import Path
import shlex
import shutil
import sys

ESPRESSIF_VID = 0x303A


def detect_port() -> str:
    override = os.environ.get("SPOT_PORT")

    from serial.tools import list_ports

    ports = sorted(
        (port for port in list_ports.comports() if port.vid == ESPRESSIF_VID),
        key=lambda port: port.device,
    )
    if override:
        if any(port.device == override for port in ports):
            return override
        raise RuntimeError("SPOT_PORT is not a connected Espressif USB serial port")
    if not ports:
        raise RuntimeError(
            "No Espressif USB serial port was found. Connect the device or set SPOT_PORT."
        )
    if len(ports) > 1:
        names = ", ".join(port.device for port in ports)
        raise RuntimeError(f"More than one Espressif USB serial port was found: {names}. Set SPOT_PORT.")
    return ports[0].device


def windows_esptool_python(path: Path) -> Path | None:
    paths = (path, path.resolve())
    candidates: list[Path] = []
    for executable in paths:
        candidates.append(executable.parent / "python.exe")
        for parent in executable.parents:
            if parent.name.lower() == "bin":
                candidates.append(parent.parent / "venvs" / "esptool" / "Scripts" / "python.exe")
            if parent.name.lower() == "venvs":
                candidates.append(parent / "esptool" / "Scripts" / "python.exe")
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def esptool_python() -> Path | None:
    executable = shutil.which("esptool") or shutil.which("esptool.py")
    if not executable:
        return None

    path = Path(executable)
    if os.name == "nt":
        return windows_esptool_python(path)

    try:
        with path.open(encoding="utf-8") as script:
            first_line = script.readline().strip()
    except (OSError, UnicodeDecodeError):
        return None
    if not first_line.startswith("#!"):
        return None
    candidate = Path(shlex.split(first_line[2:])[0])
    return candidate if candidate.exists() else None


def reexec_with_esptool_python() -> None:
    python = esptool_python()
    if python is None or os.environ.get("SPOT_PYSERIAL_REEXEC") == "1":
        raise RuntimeError("pyserial is unavailable. Run `mise install` to install esptool.")
    environment = os.environ.copy()
    environment["SPOT_PYSERIAL_REEXEC"] = "1"
    os.execve(str(python), [str(python), str(Path(__file__).resolve())], environment)


def main() -> int:
    try:
        print(detect_port())
    except ModuleNotFoundError:
        try:
            reexec_with_esptool_python()
        except RuntimeError as error:
            print(error, file=sys.stderr)
            return 1
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Canceled.", file=sys.stderr)
        raise SystemExit(130)
