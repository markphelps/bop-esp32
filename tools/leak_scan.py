#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mark Phelps
# SPDX-License-Identifier: Apache-2.0

"""Scan the publishable tree and the Git history for secrets.

Two scans, because they answer different questions. The tree scan asks what a
reader downloads today. The history scan asks what anyone can recover from the
commits, which is the question that matters once a repository is public.

The tree scan runs against the tracked files only, materialized into a
temporary directory. Scanning the working directory instead would walk
`firmware/managed_components/` and `firmware/build/`, which are hundreds of
megabytes of third-party and generated content that never reaches the public
repository, and whose findings would train a reader to ignore this check.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

# `-v` prints the rule, the file, and the line for each finding. Without it
# gitleaks prints only a count, and "review every finding" is impossible.
# `--exit-code 2` separates findings from a gitleaks internal error, which is 1.
REPORT_ARGUMENTS = ("--redact", "--no-banner", "-v", "--exit-code", "2")


def gitleaks() -> str:
    found = shutil.which("gitleaks")
    if found is None:
        raise RuntimeError("gitleaks is unavailable. Run `mise install` first.")
    return found


def materialize(revision: str, destination: Path) -> int:
    """Write the revision's tracked files into destination. Returns the file count."""
    archive = destination.parent / "tree.tar"
    with archive.open("wb") as output:
        subprocess.run(["git", "archive", "--format=tar", revision], check=True, stdout=output)
    with tarfile.open(archive) as tar:
        members = [member for member in tar.getmembers() if member.isfile()]
        tar.extractall(destination, filter="data")
    archive.unlink()
    return len(members)


def scan(
    label: str, arguments: list[str], report: Path | None = None, cwd: Path | None = None
) -> bool:
    """Run one gitleaks scan. Returns True when it found nothing.

    `cwd` is what makes a finding readable. The tree scan targets `.` from inside
    the materialized tree, so gitleaks records repository-relative paths. Given an
    absolute path it records that instead, and every path in the report would name
    a temporary directory that is erased before anyone can open it.
    """
    print(f"\n=== {label} ===", flush=True)
    if report is not None:
        report = report.resolve()
        arguments = [*arguments, "--report-format", "json", "--report-path", str(report)]
    result = subprocess.run([gitleaks(), *arguments, *REPORT_ARGUMENTS], check=False, cwd=cwd)
    if result.returncode == 0:
        return True
    if result.returncode == 2:
        print(f"{label}: gitleaks reported findings.", file=sys.stderr)
        return False
    raise RuntimeError(f"{label}: gitleaks exited {result.returncode}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", default="HEAD", help="tree to scan")
    parser.add_argument(
        "--log-opts",
        default="--all",
        help="git log options for the history scan (default: every branch and tag)",
    )
    parser.add_argument(
        "--reports", default="reports/secrets", help="directory for the JSON findings reports"
    )
    options = parser.parse_args()

    reports = Path(options.reports).resolve()
    try:
        reports.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as workspace:
            tree = Path(workspace) / "tree"
            tree.mkdir()
            count = materialize(options.revision, tree)
            print(f"Scanning {count} tracked file(s) from {options.revision}.")
            clean_tree = scan(
                f"tracked files at {options.revision}",
                ["dir", "."],
                report=reports / "tracked-files.json",
                cwd=tree,
            )
        clean_history = scan(
            "git history",
            ["git", ".", "--log-opts", options.log_opts],
            report=reports / "history.json",
        )
    except (OSError, RuntimeError, tarfile.TarError, subprocess.CalledProcessError) as error:
        print(f"secret scan failed: {error}", file=sys.stderr)
        return 1

    if clean_tree and clean_history:
        print("\nNo secret was found in the tracked files or in any branch or tag.")
        return 0
    print(
        f"\nReview every finding above before you publish. The reports are in {reports}.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Canceled.", file=sys.stderr)
        raise SystemExit(130)
