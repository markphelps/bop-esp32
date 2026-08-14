#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mark Phelps
# SPDX-License-Identifier: Apache-2.0

"""Take the factory backup, then flash. `--force` skips the backup and warns.

The backup step used to be a `depends` entry on the `flash` task. mise runs a
`depends` entry whatever arguments follow the task, so the gate had to move
here to be skippable at all.

`--force` skips TAKING a backup. It never makes a forbidden backup possible:
the forced path does not call `backup_flash` at all, and the unforced path
calls it with no arguments, so nothing a user types reaches the refusal that
stops a provisioned device from copying its WiFi password and its Spotify
refresh token to this computer.

This tool owns two arguments and forwards the rest to idf.py unchanged, in the
place idf.py wants them. See `split_arguments`.
"""

from __future__ import annotations

from pathlib import Path
import sys

import backup_flash
from device import project_root
import run_idf

FORCE_FLAG = "--force"
BUILD_DIR_FLAG = "--build-dir"


def forced_warning(image: Path) -> str:
    """One line that names the skipped step and what is left to restore from."""
    if image.exists():
        return (
            f"Warning: {FORCE_FLAG} skipped the backup step, so {image} was not verified "
            "before this flash."
        )
    return (
        f"Warning: {FORCE_FLAG} skipped the factory backup, and no factory image exists at "
        f"{image} to restore this board from."
    )


def split_arguments(arguments: list[str]) -> tuple[bool, list[str]]:
    """Read Bop's own two arguments, and place the rest where idf.py wants them.

    idf.py takes `-B` before its subcommand and every flash option after it. The
    `flash` task used to run `run_idf.py flash`, and mise appends what follows
    `--` to the end of that, so a user's arguments arrived after the subcommand.
    They still do. `--build-dir` is how `mise.toml` names the performance build
    directory, because that one has to arrive before the subcommand instead.

    Only the FIRST `--force` is Bop's. A second one reaches `idf.py flash
    --force`, which is a different flag with a different meaning, and which
    would otherwise have no spelling left.
    """
    forced = False
    build_directory: list[str] = []
    passthrough: list[str] = []
    remaining = list(arguments)

    while remaining:
        argument = remaining.pop(0)
        if argument == FORCE_FLAG and not forced:
            forced = True
        elif argument == BUILD_DIR_FLAG and remaining and not build_directory:
            build_directory = ["-B", remaining.pop(0)]
        else:
            passthrough.append(argument)

    return forced, [*build_directory, "flash", *passthrough]


def main() -> int:
    forced, arguments = split_arguments(sys.argv[1:])

    if forced:
        print(forced_warning(project_root() / "backups" / "factory.bin"), file=sys.stderr)
    else:
        # The same gate the `flash` task used to declare as a dependency. A
        # failed backup stops the flash, with the exit code the backup chose.
        code = backup_flash.main()
        if code != 0:
            return code

    return run_idf.main(arguments)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Canceled.", file=sys.stderr)
        raise SystemExit(130)
