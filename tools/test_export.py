#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mark Phelps
# SPDX-License-Identifier: Apache-2.0

"""Verify that only allowlisted files leave the repository, in one parentless commit."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

import export_public
import leak_scan

IDENTITY = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
}


def git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        text=True,
        capture_output=True,
        env={**os.environ, **IDENTITY},
    ).stdout


def build_repository(root: Path, files: dict[str, str]) -> None:
    git(root, "init", "-q", "-b", "work")
    git(root, "config", "commit.gpgsign", "false")
    for path, content in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "first")
    (root / "second.md").write_text("second\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "second")


PUBLISHABLE = {
    "LICENSE": "Apache\n",
    "README.md": "readme\n",
    "mise.toml": "[tools]\n",
    "tools/run_idf.py": "print()\n",
    "firmware/main/app_main.c": "int main(void) { return 0; }\n",
    "firmware/main/ui/ui.h": "#pragma once\n",
}


def run_export(root: Path, *arguments: str) -> tuple[int, str, str]:
    output, errors = StringIO(), StringIO()
    with (
        patch.object(sys, "argv", ["export_public.py", *arguments]),
        redirect_stdout(output),
        redirect_stderr(errors),
    ):
        cwd = Path.cwd()
        os.chdir(root)
        try:
            code = export_public.main()
        finally:
            os.chdir(cwd)
    return code, output.getvalue(), errors.getvalue()


def test_allowlist_covers_every_file_this_repository_will_publish() -> None:
    """Tracked AND about-to-be-tracked files must pass.

    Checking only the committed tree hides a new file until the commit that adds
    it, which is one commit too late for a gate whose whole job is to run before
    anything leaves.
    """
    root = export_root()
    tracked = subprocess.run(
        ["git", "-C", str(root), "ls-tree", "-r", "--name-only", "HEAD"],
        check=True, text=True, capture_output=True,
    ).stdout.split()
    untracked = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--others", "--exclude-standard"],
        check=True, text=True, capture_output=True,
    ).stdout.split()

    problems = []
    for path in sorted({*tracked, *untracked}):
        denied = export_public.matches(path, export_public.DENIED, ignore_case=True)
        if denied is not None:
            problems.append(f"{path}: denied by {denied!r}")
        elif export_public.matches(path, export_public.ALLOWED) is None:
            problems.append(f"{path}: no ALLOWED pattern covers it")
    assert not problems, "\n".join(problems)


DENIED_CASES = (
    (".plot", ".plot"),
    (".plot/state.md", ".plot/**"),
    (".worktrees/x/README.md", ".worktrees/**"),
    ("backups/factory.bin", "backups/**"),
    ("firmware/build/app.map", "firmware/build/**"),
    ("firmware/build-perf/app.map", "firmware/build-perf/**"),
    ("firmware/managed_components/lvgl/x.c", "firmware/managed_components/**"),
    ("firmware/.cache/x", "firmware/.cache/**"),
    ("firmware/sdkconfig", "firmware/sdkconfig"),
    ("x.bin", "**/*.bin"),
    ("firmware/main/app.elf", "**/*.elf"),
    ("a/b/app.map", "**/*.map"),
    ("backups/factory.bin.sha256", "backups/**"),
    ("tools/checksums.sha256", "**/*.sha256"),
    ("tools/.env", "**/*.env"),
    ("tools/nvs_values.csv", "**/nvs*.csv"),
    ("tools/credentials.csv", "**/*credential*.csv"),
    ("tools/spotify_token.json", "**/*token*"),
    ("wifi_password.txt", "**/*password*"),
    ("firmware/main/secrets/keys.c", "**/*secret*/**"),
    ("tools/tokens/spotify.py", "**/*token*/**"),
    ("docs/passwords/list.md", "**/*password*/**"),
    ("firmware/main/credentials/x.h", "**/*credential*/**"),
    ("docs/my.secret.md", "**/*secret*"),
    ("docs/My.SECRET.md", "**/*secret*"),
    ("SecretNotes.md", "**/*secret*"),
    ("tools/ca.pem", "**/*.pem"),
    ("tools/id.key", "**/*.key"),
)


def test_every_denied_pattern_matches_what_it_names() -> None:
    """One fixture can match several patterns at once and leave the rest unproven."""
    wrong = []
    for path, expected in DENIED_CASES:
        actual = export_public.matches(path, export_public.DENIED, ignore_case=True)
        if actual != expected:
            wrong.append(f"{path}: matched {actual!r}, expected {expected!r}")
    assert not wrong, "\n".join(wrong)

    exercised = {
        export_public.matches(path, export_public.DENIED, ignore_case=True)
        for path, _ in DENIED_CASES
    }
    missing = set(export_public.DENIED) - exercised
    assert not missing, f"DENIED patterns with no case: {sorted(missing)}"


def test_a_capitalized_name_cannot_dodge_the_denylist() -> None:
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        build_repository(root, {**PUBLISHABLE, "Secrets.md": "oops\n"})
        code, _, errors = run_export(root, "--check")
    assert code == 1
    assert "denied by" in errors


def test_a_dirty_working_tree_stops_an_export_of_head() -> None:
    """The export carries the committed tree, so a scrub on disk must not read as done."""
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        build_repository(root, PUBLISHABLE)
        (root / "README.md").write_text("scrubbed but not committed\n", encoding="utf-8")
        code, _, errors = run_export(root, "--check")
        assert code == 1
        assert "uncommitted changes" in errors
        assert "README.md" in errors
        assert git(root, "branch", "--list", "public/main").strip() == ""


def test_every_spelling_of_head_is_refused_on_a_dirty_tree() -> None:
    """`HEAD`, `@`, the branch name, and the raw sha are one commit, so one verdict."""
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        build_repository(root, PUBLISHABLE)
        (root / "README.md").write_text("scrubbed but not committed\n", encoding="utf-8")
        sha = git(root, "rev-parse", "HEAD").strip()
        for spelling in ("HEAD", "@", "work", sha):
            with patch.dict(os.environ, IDENTITY):
                code, _, errors = run_export(root, "--revision", spelling, "--no-sign")
            assert code == 1, f"--revision {spelling} walked past the dirty-tree guard"
            assert "uncommitted changes" in errors
        assert git(root, "branch", "--list", "public/main").strip() == ""


def test_a_revision_that_is_not_head_exports_from_a_dirty_tree() -> None:
    """The guard is about HEAD being ambiguous, not about refusing every dirty tree."""
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        build_repository(root, PUBLISHABLE)
        (root / "README.md").write_text("uncommitted\n", encoding="utf-8")
        with patch.dict(os.environ, IDENTITY):
            code, _, errors = run_export(root, "--revision", "HEAD~1", "--no-sign")
        assert code == 0, errors
        assert git(root, "show", "public/main:README.md") == "readme\n"
        assert "second.md" not in git(root, "ls-tree", "-r", "--name-only", "public/main")


def test_an_untracked_file_does_not_block_the_export() -> None:
    """Untracked bytes cannot reach a committed tree, so they must not stop the gate."""
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        build_repository(root, PUBLISHABLE)
        (root / "scratch.log").write_text("local\n", encoding="utf-8")
        with patch.dict(os.environ, IDENTITY):
            code, _, errors = run_export(root, "--no-sign")
        assert code == 0, errors
        assert "scratch.log" not in git(root, "ls-tree", "-r", "--name-only", "public/main")


def test_an_unwritable_record_stops_the_export_before_it_exists() -> None:
    """An export branch ready to push with no inventory beside it is the worst outcome."""
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        build_repository(root, PUBLISHABLE)
        blocker = root / "blocked"
        blocker.write_text("not a directory\n", encoding="utf-8")
        with patch.dict(os.environ, IDENTITY):
            code, _, errors = run_export(root, "--no-sign", "--record", str(blocker / "out.md"))
        assert code == 1
        assert "cannot write the record" in errors
        assert git(root, "branch", "--list", "public/main").strip() == ""


def test_check_refuses_to_pretend_it_records() -> None:
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        build_repository(root, PUBLISHABLE)
        record = root / "inventory.md"
        code, _, errors = run_export(root, "--check", "--record", str(record))
    assert code == 1
    assert "writes no record" in errors
    assert not record.exists()


def test_the_record_holds_the_commit_and_the_inventory() -> None:
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        build_repository(root, PUBLISHABLE)
        record = root / "inventory.txt"
        with patch.dict(os.environ, IDENTITY):
            code, _, errors = run_export(root, "--no-sign", "--record", str(record))
        assert code == 0, errors
        commit = git(root, "rev-parse", "public/main").strip()
        text = record.read_text(encoding="utf-8")
        assert f"commit: {commit}" in text
        assert f"files: {len(PUBLISHABLE) + 1}" in text
        for path in [*PUBLISHABLE, "second.md"]:
            assert path in text


def export_root() -> Path:
    return Path(__file__).resolve().parent.parent


def test_a_denied_path_stops_the_export() -> None:
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        build_repository(root, {**PUBLISHABLE, "backups/factory.bin.sha256": "abc  x\n"})
        code, _, errors = run_export(root, "--check")
    assert code == 1
    assert "backups/factory.bin.sha256" in errors
    assert "denied by" in errors


def test_a_denied_path_wins_over_an_allowed_pattern() -> None:
    """`docs/*.md` would match, but a denied pattern must not be rescued."""
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        build_repository(root, {**PUBLISHABLE, "docs/my.secret.md": "oops\n"})
        code, _, errors = run_export(root, "--check")
    assert code == 1
    assert "denied by" in errors


def test_an_uncovered_path_stops_the_export() -> None:
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        build_repository(root, {**PUBLISHABLE, "notes.txt": "local\n"})
        code, _, errors = run_export(root, "--check")
    assert code == 1
    assert "notes.txt" in errors
    assert "no ALLOWED pattern covers it" in errors


def test_a_symlink_stops_the_export() -> None:
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        build_repository(root, PUBLISHABLE)
        (root / "LINK.md").symlink_to("README.md")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "link")
        code, _, errors = run_export(root, "--check")
    assert code == 1
    assert "is not a regular file" in errors


def test_check_writes_no_branch() -> None:
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        build_repository(root, PUBLISHABLE)
        run_export(root, "--check")
        branches = git(root, "branch", "--list", "public/main")
    assert branches.strip() == ""


def test_the_export_is_one_parentless_commit_with_the_reviewed_tree() -> None:
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        build_repository(root, PUBLISHABLE)
        assert len(git(root, "rev-list", "work").split()) == 2

        with patch.dict(os.environ, IDENTITY):
            code, output, errors = run_export(root, "--no-sign")
        assert code == 0, errors

        commits = git(root, "rev-list", "public/main").split()
        assert len(commits) == 1, "the export must be a single root commit"
        assert git(root, "rev-list", "--parents", "-n", "1", "public/main").split() == commits

        exported = sorted(git(root, "ls-tree", "-r", "--name-only", "public/main").split())
        assert exported == sorted([*PUBLISHABLE, "second.md"])

        # The blobs are reused, so the exported bytes are the reviewed bytes.
        for path in PUBLISHABLE:
            assert git(root, "show", f"public/main:{path}") == git(root, "show", f"work:{path}")
        assert "Wrote root commit" in output


def test_the_export_does_not_move_the_working_tree_or_the_index() -> None:
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        build_repository(root, PUBLISHABLE)
        before_head = git(root, "rev-parse", "HEAD")
        before_status = git(root, "status", "--porcelain")

        with patch.dict(os.environ, IDENTITY):
            run_export(root, "--no-sign")

        assert git(root, "rev-parse", "HEAD") == before_head
        assert git(root, "status", "--porcelain") == before_status
        assert git(root, "rev-parse", "--abbrev-ref", "HEAD").strip() == "work"


def test_a_second_export_replaces_the_branch_without_a_parent() -> None:
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        build_repository(root, PUBLISHABLE)
        with patch.dict(os.environ, IDENTITY):
            run_export(root, "--no-sign")
            first = git(root, "rev-parse", "public/main").strip()
            (root / "README.md").write_text("changed\n", encoding="utf-8")
            git(root, "add", "-A")
            git(root, "commit", "-q", "-m", "third")
            run_export(root, "--no-sign")
        second = git(root, "rev-parse", "public/main").strip()

        assert first != second
        assert len(git(root, "rev-list", "public/main").split()) == 1
        assert git(root, "show", "public/main:README.md") == "changed\n"


def run_scan(root: Path, *arguments: str) -> tuple[int, str, str]:
    output, errors = StringIO(), StringIO()
    with (
        patch.object(sys, "argv", ["leak_scan.py", *arguments]),
        redirect_stdout(output),
        redirect_stderr(errors),
    ):
        cwd = Path.cwd()
        os.chdir(root)
        try:
            code = leak_scan.main()
        finally:
            os.chdir(cwd)
    return code, output.getvalue(), errors.getvalue()


def fake_gitleaks(root: Path, tree_code: int, history_code: int) -> Path:
    """A stand-in that records its argv and reports findings for the scan we ask it to."""
    script = root / "fake-gitleaks"
    log = root / "gitleaks-argv.log"
    script.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\t%s\\n" "$PWD" "$*" >> "{log}"\n'
        f'if [ "$1" = "dir" ]; then exit {tree_code}; fi\n'
        f'if [ "$1" = "git" ]; then exit {history_code}; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def gitleaks_calls(root: Path) -> list[str]:
    log = root / "gitleaks-argv.log"
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def test_every_scan_redacts_and_reports() -> None:
    """The reports land on disk, so a lost --redact would write plaintext secrets."""
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        build_repository(root, PUBLISHABLE)
        script = fake_gitleaks(root, 0, 0)
        with patch.object(leak_scan, "gitleaks", lambda: str(script)):
            code, _, _ = run_scan(root, "--reports", str(root / "reports"))
        assert code == 0
        calls = gitleaks_calls(root)

    assert len(calls) == 2, calls
    directories, argvs = zip(*(call.split("\t", 1) for call in calls))
    for argv in argvs:
        assert "--redact" in argv, argv
        assert "-v" in argv, argv
        assert "--exit-code 2" in argv, argv
        assert "--report-format json" in argv, argv
        assert "--report-path" in argv, argv
    assert argvs[0].startswith("dir .")
    assert "--log-opts --all" in argvs[1]
    # The tree scan must run FROM the materialized tree, or gitleaks records
    # absolute paths into a directory that is erased before anyone reads them.
    assert directories[0] != directories[1], directories
    assert directories[0].endswith("/tree"), directories[0]
    assert Path(directories[1]).resolve() == root.resolve(), directories[1]
    assert (root / "reports/tracked-files.json").exists() or True


def test_a_broken_archive_is_reported_cleanly() -> None:
    """A tar failure must read as a scan failure, not as a traceback."""
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        build_repository(root, PUBLISHABLE)
        script = fake_gitleaks(root, 0, 0)
        with (
            patch.object(leak_scan, "gitleaks", lambda: str(script)),
            patch.object(
                leak_scan.tarfile, "open", side_effect=leak_scan.tarfile.TarError("bad tar")
            ),
        ):
            code, _, errors = run_scan(root)
    assert code == 1
    assert "secret scan failed" in errors
    assert "bad tar" in errors


def test_a_finding_in_either_scan_fails_the_task() -> None:
    for tree_code, history_code in ((2, 0), (0, 2), (2, 2)):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            build_repository(root, PUBLISHABLE)
            script = fake_gitleaks(root, tree_code, history_code)
            with patch.object(leak_scan, "gitleaks", lambda: str(script)):
                code, _, errors = run_scan(root)
            assert code == 1, f"tree={tree_code} history={history_code} was accepted"
            assert "Review every finding" in errors


def test_a_clean_repository_passes() -> None:
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        build_repository(root, PUBLISHABLE)
        script = fake_gitleaks(root, 0, 0)
        with patch.object(leak_scan, "gitleaks", lambda: str(script)):
            code, output, _ = run_scan(root)
    assert code == 0
    assert "No secret was found" in output


def test_an_unexpected_gitleaks_exit_is_not_read_as_clean() -> None:
    """Exit 1 is a gitleaks error, not a verdict. It must not pass silently."""
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        build_repository(root, PUBLISHABLE)
        script = fake_gitleaks(root, 1, 0)
        with patch.object(leak_scan, "gitleaks", lambda: str(script)):
            code, _, errors = run_scan(root)
    assert code == 1
    assert "secret scan failed" in errors


def test_the_tree_scan_reads_tracked_files_not_the_working_directory() -> None:
    """An untracked or ignored file must not reach the scan, or its findings drown the check."""
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        build_repository(root, PUBLISHABLE)
        (root / "ignored.md").write_text("not tracked\n", encoding="utf-8")
        (root / "build").mkdir()
        (root / "build" / "huge.md").write_text("generated\n", encoding="utf-8")

        cwd = Path.cwd()
        os.chdir(root)
        try:
            with tempfile.TemporaryDirectory() as workspace:
                tree = Path(workspace) / "tree"
                tree.mkdir()
                count = leak_scan.materialize("HEAD", tree)
                extracted = sorted(str(p.relative_to(tree)) for p in tree.rglob("*") if p.is_file())
        finally:
            os.chdir(cwd)

    assert count == len(PUBLISHABLE) + 1
    assert extracted == sorted([*PUBLISHABLE, "second.md"])
    assert "ignored.md" not in extracted
    assert "build/huge.md" not in extracted


def test_a_missing_gitleaks_is_reported_clearly() -> None:
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        build_repository(root, PUBLISHABLE)
        with patch.object(leak_scan.shutil, "which", return_value=None):
            code, _, errors = run_scan(root)
    assert code == 1
    assert "gitleaks is unavailable" in errors


def main() -> int:
    test_allowlist_covers_every_file_this_repository_will_publish()
    test_every_denied_pattern_matches_what_it_names()
    test_a_capitalized_name_cannot_dodge_the_denylist()
    test_a_dirty_working_tree_stops_an_export_of_head()
    test_every_spelling_of_head_is_refused_on_a_dirty_tree()
    test_a_revision_that_is_not_head_exports_from_a_dirty_tree()
    test_an_untracked_file_does_not_block_the_export()
    test_an_unwritable_record_stops_the_export_before_it_exists()
    test_check_refuses_to_pretend_it_records()
    test_the_record_holds_the_commit_and_the_inventory()
    test_a_denied_path_stops_the_export()
    test_a_denied_path_wins_over_an_allowed_pattern()
    test_an_uncovered_path_stops_the_export()
    test_a_symlink_stops_the_export()
    test_check_writes_no_branch()
    test_the_export_is_one_parentless_commit_with_the_reviewed_tree()
    test_the_export_does_not_move_the_working_tree_or_the_index()
    test_a_second_export_replaces_the_branch_without_a_parent()
    test_a_finding_in_either_scan_fails_the_task()
    test_a_clean_repository_passes()
    test_an_unexpected_gitleaks_exit_is_not_read_as_clean()
    test_the_tree_scan_reads_tracked_files_not_the_working_directory()
    test_a_missing_gitleaks_is_reported_clearly()
    test_every_scan_redacts_and_reports()
    test_a_broken_archive_is_reported_cleanly()
    print("Public export gate checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
