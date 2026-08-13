#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mark Phelps
# SPDX-License-Identifier: Apache-2.0

"""Verify the promises the CI workflow makes to a contributor.

These promises cannot be read from a green run. A workflow that builds the
firmware from another ESP-IDF release than `mise run build` still passes. So
does a workflow whose check names no longer match the branch rules, one that
follows a mutable tag, one whose firmware job stopped building anything, one
whose secret scan reads one commit instead of every branch and tag, and one
that took a write token it does not need.

This check reads the workflow as text, because the repository installs no YAML
parser and the standard library holds none. The reader below is small on
purpose: it finds each job block, and each `uses:` line inside it.
"""

from __future__ import annotations

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github/workflows/ci.yml"
SETUP = ROOT / "tools/setup_idf.py"

# The exact required-check names. The branch rules and `CLAUDE.md` name these
# same five strings. GitHub takes each one from a job name, or from the job id
# when the job declares no name.
REQUIRED_CHECKS = {
    "firmware",
    "host-tools (linux)",
    "python-style",
    "secrets",
    "licenses",
}

# The local task behind each host job. A job that stops running its task keeps
# its name and its green mark, and checks nothing.
HOST_JOB_TASKS = {
    "host-tools": ("test-host",),
    "python-style": ("format-check", "lint"),
    "secrets": ("secrets",),
    "licenses": ("licenses",),
}

# Steps that publish build output. This repository is source only.
FORBIDDEN_STEPS = ("upload-artifact", "release", "docker push")


def workflow_source() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def workflow_steps() -> str:
    """The workflow with every comment removed.

    The comments name releases and explain the pinning rules, so a search for a
    publishing step finds them and reports a workflow that publishes nothing.
    """
    lines = []
    for line in workflow_source().splitlines():
        text = re.sub(r"\s+#.*$", "", line)
        if text.strip() and not text.lstrip().startswith("#"):
            lines.append(text)
    return "\n".join(lines)


def job_blocks() -> dict[str, str]:
    """Map each job id to its own block of the workflow.

    A job id is the only key at two-space indentation under `jobs:`. Every key
    inside a job is deeper than that.
    """
    source = workflow_source()
    start = source.index("\njobs:\n")
    body = source[start:]
    heads = list(re.finditer(r"^ {2}([\w-]+):$", body, re.MULTILINE))
    assert heads, "the workflow declares no job"
    ends = [head.start() for head in heads[1:]] + [len(body)]
    return {head.group(1): body[head.start() : end] for head, end in zip(heads, ends)}


def job_block(job_id: str) -> str:
    block = job_blocks().get(job_id)
    assert block is not None, f"the workflow declares no job with the id {job_id}"
    return block


def test_check_names_are_the_required_checks() -> None:
    names = set()
    for job_id, block in job_blocks().items():
        declared = re.search(r"^ {4}name: (.+)$", block, re.MULTILINE)
        names.add(job_id if declared is None else declared.group(1))
    assert names == REQUIRED_CHECKS, f"check names {sorted(names)} are not the required checks"


def test_the_workflow_takes_no_write_permission() -> None:
    source = workflow_source()
    assert re.search(r"^permissions:\n {2}contents: read\n", source, re.MULTILINE), (
        "the workflow does not set read-only default permissions"
    )
    assert re.search(r"^ {4}permissions:", source, re.MULTILINE) is None, (
        "a job raises its own permissions"
    )


def test_firmware_job_builds_the_pinned_esp_idf_release() -> None:
    pinned = re.search(r'^IDF_TAG = "(v[^"]+)"', SETUP.read_text(encoding="utf-8"), re.MULTILINE)
    assert pinned is not None, "tools/setup_idf.py does not pin IDF_TAG"

    container = re.search(r"^ {4}container: (\S+)(.*)$", job_block("firmware"), re.MULTILINE)
    assert container is not None, "the firmware job names no container"

    image, comment = container.group(1), container.group(2)
    assert re.fullmatch(r"espressif/idf@sha256:[0-9a-f]{64}", image), f"{image} is not a digest"
    assert comment.strip() == f"# {pinned.group(1)}", (
        f"the container comment is {comment.strip()!r} and setup_idf.py pins {pinned.group(1)}"
    )


def test_firmware_job_builds_and_then_holds_the_dependency_lock() -> None:
    block = job_block("firmware")
    build = "idf.py -C firmware build"
    guard = "git diff --exit-code -- firmware/dependencies.lock"
    assert build in block, "the firmware job builds nothing"
    assert guard in block, "the firmware job does not prove that the build used the committed lock"
    assert block.index(build) < block.index(guard), "the lock guard runs before the build"


def test_each_host_job_runs_its_local_task() -> None:
    for job_id, tasks in HOST_JOB_TASKS.items():
        block = job_block(job_id)
        for task in tasks:
            assert f"mise run {task}" in block, f"the {job_id} job does not run mise run {task}"


def test_the_secret_scan_reads_the_complete_history() -> None:
    assert "fetch-depth: 0" in job_block("secrets"), (
        "the secrets job checks out one commit, so the history scan would read almost nothing"
    )


def test_every_action_is_pinned_to_a_commit() -> None:
    lines = list(re.finditer(r"^\s*(?:- )?uses: (\S+)(.*)$", workflow_source(), re.MULTILINE))
    assert lines, "the workflow uses no action, so this check would prove nothing"
    for line in lines:
        action, comment = line.group(1), line.group(2)
        assert re.fullmatch(r"[\w.\-/]+@[0-9a-f]{40}", action), f"{action} is not a commit SHA"
        assert comment.strip().startswith("# v"), f"{action} does not name its release in a comment"


def test_no_job_publishes_a_binary() -> None:
    steps = workflow_steps()
    for step in FORBIDDEN_STEPS:
        assert step not in steps, f"CI runs {step}, which can publish build output"


def main() -> int:
    test_check_names_are_the_required_checks()
    test_the_workflow_takes_no_write_permission()
    test_firmware_job_builds_the_pinned_esp_idf_release()
    test_firmware_job_builds_and_then_holds_the_dependency_lock()
    test_each_host_job_runs_its_local_task()
    test_the_secret_scan_reads_the_complete_history()
    test_every_action_is_pinned_to_a_commit()
    test_no_job_publishes_a_binary()
    print("CI workflow checks passed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Canceled.", file=sys.stderr)
        raise SystemExit(130)
