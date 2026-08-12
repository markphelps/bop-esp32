#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mark Phelps
# SPDX-License-Identifier: Apache-2.0

"""Build the one clean root commit that may leave this repository.

The export carries no development ancestry. It reuses the blobs that the
exported revision already holds, so the exported bytes are the reviewed bytes,
and it writes a single parentless commit on its own branch.

Every tracked path must match one ALLOWED pattern and no DENIED pattern. A path
that matches neither list stops the export. That is deliberate: a new file is
either something the public repository should carry, and belongs in ALLOWED, or
it is not, and the loud failure is the point.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys

EXPORT_BRANCH = "public/main"

# Nothing outside this list may leave the repository.
ALLOWED = (
    ".gitignore",
    "LICENSE",
    "LICENSES/*.txt",
    "REUSE.toml",
    "mise.toml",
    "*.md",
    "docs/*.md",
    ".github/workflows/*.yml",
    "firmware/CMakeLists.txt",
    "firmware/dependencies.lock",
    "firmware/partitions.csv",
    "firmware/sdkconfig.defaults",
    "firmware/sdkconfig.perf.defaults",
    "firmware/main/CMakeLists.txt",
    "firmware/main/idf_component.yml",
    "firmware/main/**/*.c",
    "firmware/main/**/*.h",
    "firmware/main/ui/fonts/*.ttf",
    "tools/*.py",
    "tools/esp-idf-monitor.cfg",
)

# Checked first, so a denied path cannot be rescued by a broad ALLOWED pattern.
DENIED = (
    ".plot",
    ".plot/**",
    ".worktrees/**",
    "backups/**",
    "firmware/build/**",
    "firmware/build-perf/**",
    "firmware/managed_components/**",
    "firmware/.cache/**",
    "firmware/sdkconfig",
    "**/*.bin",
    "**/*.elf",
    "**/*.map",
    "**/*.sha256",
    "**/*.env",
    "**/nvs*.csv",
    "**/*credential*.csv",
    "**/*token*",
    "**/*password*",
    "**/*secret*/**",
    "**/*token*/**",
    "**/*password*/**",
    "**/*credential*/**",
    "**/*secret*",
    "**/*.pem",
    "**/*.key",
)

# git file modes. Anything else is a symlink or a submodule, and neither belongs
# in an export whose whole purpose is that a reader can see what they get.
REGULAR_MODES = {"100644", "100755"}


def run(*arguments: str, index: Path | None = None) -> str:
    """Run git. With `index`, run against a scratch index instead of the real one."""
    environment = dict(os.environ)
    if index is not None:
        environment["GIT_INDEX_FILE"] = str(index)
    return subprocess.run(
        ["git", *arguments], check=True, text=True, capture_output=True, env=environment
    ).stdout


def matches(path: str, patterns: tuple[str, ...], *, ignore_case: bool = False) -> str | None:
    """Return the first matching pattern, or None.

    DENIED matching ignores case. `full_match` is case-sensitive on a POSIX
    flavour, so `**/*secret*` alone would admit `Secrets.md` through the broad
    `*.md` rule. A denylist that a capital letter defeats is not a denylist.
    """
    candidate = PurePosixPath(path.lower() if ignore_case else path)
    return next((pattern for pattern in patterns if candidate.full_match(pattern)), None)


def tracked_entries(revision: str) -> list[tuple[str, str, str]]:
    """Return (mode, object id, path) for every file in the revision's tree.

    `-z` matters. Without it git C-quotes any path holding a non-ASCII or control
    character, unless `core.quotePath` is false, so the same tree would get two
    different verdicts on two machines depending on a setting nobody records.
    """
    entries = []
    output = run("ls-tree", "-r", "--full-tree", "-z", revision)
    for record in output.split("\0"):
        if not record:
            continue
        metadata, path = record.split("\t", 1)
        mode, kind, object_id = metadata.split()
        if kind != "blob":
            raise SystemExit(f"{path} is a {kind}, not a file")
        entries.append((mode, object_id, path))
    return entries


def review(entries: list[tuple[str, str, str]]) -> list[str]:
    """Return the reasons this tree cannot be exported. Empty means it can."""
    problems = []
    for mode, _object_id, path in entries:
        denied = matches(path, DENIED, ignore_case=True)
        if denied is not None:
            problems.append(f"{path}: denied by {denied!r}")
            continue
        if matches(path, ALLOWED) is None:
            problems.append(f"{path}: no ALLOWED pattern covers it")
        if mode not in REGULAR_MODES:
            problems.append(f"{path}: mode {mode} is not a regular file")
    return problems


def write_export_commit(entries: list[tuple[str, str, str]], message: str, sign: bool) -> str:
    """Write one parentless commit holding exactly these entries.

    A scratch index keeps the real index and the working tree untouched, and the
    entries carry the object ids from the reviewed tree, so nothing is re-read
    from disk between the review and the commit.
    """
    index = Path(run("rev-parse", "--git-dir").strip()) / "export-index"
    index.unlink(missing_ok=True)
    try:
        run("read-tree", "--empty", index=index)
        for mode, object_id, path in entries:
            run("update-index", "--add", "--cacheinfo", f"{mode},{object_id},{path}", index=index)
        tree = run("write-tree", index=index).strip()
    finally:
        index.unlink(missing_ok=True)
    arguments = ["commit-tree", tree, "-m", message]
    if sign:
        arguments.insert(1, "-S")
    return run(*arguments).strip()


def uncommitted_changes() -> str:
    """Return the porcelain status of TRACKED files only.

    Untracked files cannot reach the export, because it reads a committed tree.
    Refusing on them would block the gate on any stray file and push an operator
    toward a bypass.
    """
    return run("status", "--porcelain", "--untracked-files=no").strip()


def resolve_commit(revision: str) -> str | None:
    """Return the commit id a revision names, or None when it names no commit.

    The export exports a commit, never a bare tree. `HEAD:` and `HEAD^{tree}`
    name a tree, and a guard that only compares commits would skip them and
    export the tree anyway. Refusing them here closes that, and comparing the
    resolved id closes the rest: `HEAD`, `@`, the branch name, and the raw sha
    are all the same commit however they are spelled.
    """
    try:
        return run("rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}").strip()
    except subprocess.CalledProcessError:
        return None


def inventory_record(commit: str, branch: str, entries: list[tuple[str, str, str]]) -> str:
    lines = [
        "# Bop public export inventory",
        "",
        f"commit: {commit}",
        f"branch: {branch}",
        f"files: {len(entries)}",
        "",
    ]
    lines += [f"{mode} {object_id} {path}" for mode, object_id, path in entries]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", default="HEAD", help="commit to export")
    parser.add_argument("--branch", default=EXPORT_BRANCH, help="branch to move to the export")
    parser.add_argument("--message", default="Bop", help="root commit message")
    parser.add_argument(
        "--check", action="store_true", help="review the tree and print the inventory only"
    )
    parser.add_argument("--no-sign", action="store_true", help="do not sign the root commit")
    parser.add_argument("--record", help="write the commit id and the inventory to this file")
    options = parser.parse_args()

    # The export reads a committed tree. Uncommitted work is invisible to it, so
    # a dirty tree means the operator is looking at bytes the export will not
    # carry. Refuse rather than write a stale export that reads as a success.
    commit_id = resolve_commit(options.revision)
    if commit_id is None:
        print(
            f"{options.revision} does not name a commit. Give a commit, not a tree.",
            file=sys.stderr,
        )
        return 1

    if commit_id == resolve_commit("HEAD"):
        dirty = uncommitted_changes()
        if dirty:
            print("The working tree has uncommitted changes:", file=sys.stderr)
            print(dirty, file=sys.stderr)
            print(
                "\nCommit or stash them, then export again. The export carries the "
                "committed tree, never what is on disk.",
                file=sys.stderr,
            )
            return 1

    if options.check and options.record is not None:
        print("--check writes no record. Drop one of the two options.", file=sys.stderr)
        return 1

    record = Path(options.record) if options.record is not None else None
    if record is not None:
        # Prove the record can be written before the export exists. An export
        # branch ready to push with no inventory beside it is the worst outcome.
        # Probe with a sibling file, so a run that stops later leaves nothing
        # behind that reads as an inventory of nothing.
        if record.is_dir():
            print(f"export failed: {record} is a directory", file=sys.stderr)
            return 1
        try:
            record.parent.mkdir(parents=True, exist_ok=True)
            probe = record.with_name(record.name + ".probe")
            probe.touch()
            probe.unlink()
        except OSError as error:
            print(f"export failed: cannot write the record: {error}", file=sys.stderr)
            return 1

    entries = tracked_entries(commit_id)
    problems = review(entries)

    print(f"{len(entries)} file(s) in {options.revision} ({commit_id[:12]}):")
    for mode, object_id, path in entries:
        print(f"  {mode} {object_id[:12]} {path}")

    if problems:
        print(f"\n{len(problems)} problem(s):", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        print("\nThe export was not written.", file=sys.stderr)
        return 1

    print("\nEvery file matches the allowlist and no denied pattern.")
    if options.check:
        return 0

    commit = write_export_commit(entries, options.message, sign=not options.no_sign)
    parents = run("rev-list", "--parents", "-n", "1", commit).split()
    if len(parents) != 1:
        raise SystemExit(f"{commit} has a parent. The export must be a root commit.")
    # Stage the record, move the branch, then put the record in place. Either
    # step can fail, and neither order alone is safe: writing the record first
    # leaves a file claiming an export that was never published, and moving the
    # branch first leaves an export with no inventory.
    #
    # Staging closes the larger window, not every window. If `os.replace` fails
    # the branch has already moved, and the message below says so.
    staged = None
    if record is not None:
        staged = record.with_name(record.name + ".new")
        staged.write_text(inventory_record(commit, options.branch, entries), encoding="utf-8")
    try:
        run("branch", "--force", options.branch, commit)
    except subprocess.CalledProcessError:
        if staged is not None:
            staged.unlink(missing_ok=True)
        raise
    if staged is not None and record is not None:
        try:
            os.replace(staged, record)
        except OSError as error:
            print(
                f"The branch moved to {commit}, but the record did not land: {error}\n"
                f"The inventory is still at {staged}.",
                file=sys.stderr,
            )
            return 1
        print(f"Recorded the commit id and the inventory in {record}.")
    print(f"\nWrote root commit {commit} to {options.branch}.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as error:
        print(f"export failed: {error.stderr or error}", file=sys.stderr)
        raise SystemExit(1)
    except OSError as error:
        print(f"export failed: {error}", file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        print("Canceled.", file=sys.stderr)
        raise SystemExit(130)
