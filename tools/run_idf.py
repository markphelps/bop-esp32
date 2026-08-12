#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mark Phelps
# SPDX-License-Identifier: Apache-2.0

"""Run idf.py after loading the pinned ESP-IDF environment."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

PORT_COMMANDS = {"flash", "monitor"}


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def idf_path() -> Path:
    configured = os.environ.get("SPOT_IDF_PATH")
    return Path(configured).expanduser() if configured else Path.home() / ".local/share/esp-idf"


def detect_port() -> str:
    result = subprocess.run(
        [sys.executable, str(project_root() / "tools" / "detect_port.py")],
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def idf_arguments(arguments: list[str]) -> list[str]:
    if any(argument in PORT_COMMANDS for argument in arguments):
        return ["-p", detect_port(), *arguments]
    return arguments


def perf_arguments(arguments: list[str], firmware: Path) -> list[str]:
    if os.environ.get("SPOT_PERF_MONITOR") != "1":
        return arguments
    sdkconfig = firmware / "build-perf" / "sdkconfig"
    defaults = "SDKCONFIG_DEFAULTS=sdkconfig.defaults;sdkconfig.perf.defaults"
    return ["-D", f"SDKCONFIG={sdkconfig}", "-D", defaults, *arguments]


def run_idf(arguments: list[str]) -> int:
    root = idf_path()
    firmware = project_root() / "firmware"
    arguments = perf_arguments(idf_arguments(arguments), firmware)

    if os.name == "nt":
        export = root / "export.bat"
        command = f'call "{export}" >nul && idf.py {subprocess.list2cmdline(arguments)}'
        return subprocess.run(["cmd.exe", "/d", "/s", "/c", command], cwd=firmware).returncode

    export = root / "export.sh"
    script = '. "$1" >/dev/null && shift && exec idf.py "$@"'
    return subprocess.run(
        ["bash", "-c", script, "idf-wrapper", str(export), *arguments], cwd=firmware
    ).returncode


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: run_idf.py <idf.py arguments...>", file=sys.stderr)
        return 2
    try:
        return run_idf(sys.argv[1:])
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"idf.py failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Canceled.", file=sys.stderr)
        raise SystemExit(130)
