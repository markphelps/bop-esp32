#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mark Phelps
# SPDX-License-Identifier: Apache-2.0

"""Install the pinned ESP-IDF release for the ESP32-S3."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

IDF_TAG = "v5.5.5"
MARKER = ".spot-install-version"


def run(command: list[str], *, cwd: Path | None = None) -> None:
    print("+", subprocess.list2cmdline(command), flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def idf_path() -> Path:
    configured = os.environ.get("SPOT_IDF_PATH")
    return Path(configured).expanduser() if configured else Path.home() / ".local/share/esp-idf"


def clone_idf(destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "git",
            "clone",
            "--branch",
            IDF_TAG,
            "--depth",
            "1",
            "--recursive",
            "--shallow-submodules",
            "https://github.com/espressif/esp-idf.git",
            str(destination),
        ]
    )


def make_sure_checkout_is_pinned(destination: Path) -> None:
    if not (destination / ".git").is_dir():
        raise RuntimeError(f"{destination} exists but is not an ESP-IDF Git checkout")

    head = subprocess.check_output(
        ["git", "-C", str(destination), "rev-parse", "HEAD"], text=True
    ).strip()
    expected = subprocess.check_output(
        ["git", "-C", str(destination), "rev-list", "-n", "1", IDF_TAG], text=True
    ).strip()
    if head != expected:
        raise RuntimeError(
            f"{destination} is at {head[:12]}, not {IDF_TAG}. "
            "Move it or set SPOT_IDF_PATH to an empty path."
        )

    run(
        ["git", "-C", str(destination), "submodule", "update", "--init", "--recursive", "--depth", "1"]
    )


def install_tools(destination: Path) -> None:
    marker = destination / MARKER
    if marker.exists() and marker.read_text(encoding="utf-8").strip() == IDF_TAG:
        print(f"ESP-IDF {IDF_TAG} is already installed at {destination}")
        return

    if os.name == "nt":
        run(["cmd.exe", "/d", "/s", "/c", "install.bat esp32s3"], cwd=destination)
    else:
        run(["bash", "install.sh", "esp32s3"], cwd=destination)

    marker.write_text(f"{IDF_TAG}\n", encoding="utf-8")
    print(f"Installed ESP-IDF {IDF_TAG} at {destination}")


def main() -> int:
    destination = idf_path()
    try:
        if not destination.exists():
            clone_idf(destination)
        make_sure_checkout_is_pinned(destination)
        install_tools(destination)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"setup failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Canceled.", file=sys.stderr)
        raise SystemExit(130)
