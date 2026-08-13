#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mark Phelps
# SPDX-License-Identifier: Apache-2.0

"""Verify the legal-consent and temporary-file controls for provisioning."""

from __future__ import annotations

from contextlib import redirect_stdout
import csv
from io import StringIO
from pathlib import Path
import re
import tempfile
from unittest.mock import patch

import provision

ROOT = Path(__file__).resolve().parent.parent


def firmware_nvs_namespace() -> str:
    source = (ROOT / "firmware/main/credentials.c").read_text(encoding="utf-8")
    match = re.search(r'#define\s+BOP_NVS_NAMESPACE\s+"([^"]*)"', source)
    assert match is not None, "credentials.c does not define BOP_NVS_NAMESPACE"
    return match.group(1)


def test_nvs_namespace_matches_the_firmware() -> None:
    assert provision.NVS_NAMESPACE == firmware_nvs_namespace()


def test_written_csv_uses_the_firmware_nvs_namespace() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "credentials.csv"
        provision.write_nvs_csv(path, "Test WiFi", "password", "client-id", "refresh-token")
        rows = list(csv.reader(path.read_text(encoding="utf-8").splitlines()))

    # nvs_partition_gen scopes each key to the namespace row above it, so the
    # namespace must come first and must be the only one. A second namespace
    # row would move the keys after it out of the namespace the firmware opens.
    assert rows[0] == ["key", "type", "encoding", "value"]
    assert rows[1] == [firmware_nvs_namespace(), "namespace", "", ""]
    assert [row for row in rows[2:] if row[1] == "namespace"] == []


def test_legal_documents_are_available() -> None:
    output = StringIO()
    with redirect_stdout(output):
        provision.print_legal_documents()

    for name in ("EULA.md", "PRIVACY.md"):
        path = ROOT / name
        assert path.is_file()
        assert f"{name}: {path.as_uri()}" in output.getvalue()


def test_refusal_stops_before_authorization() -> None:
    with (
        patch("builtins.input", return_value="I AGREE "),
        patch.object(provision, "authorize") as authorize,
    ):
        assert provision.main() == 1

    authorize.assert_not_called()


def test_acceptance_starts_authorization_after_consent() -> None:
    events: list[str] = []

    def agree(prompt: str) -> str:
        events.append(prompt)
        return "I AGREE"

    def authorize(*_args: object) -> str:
        events.append("authorize")
        raise RuntimeError("stop after authorization")

    with (
        patch("builtins.input", side_effect=agree),
        patch.object(provision, "authorize", side_effect=authorize),
        patch.object(provision, "prompt_value", side_effect=["Test WiFi", "password", "client-id"]),
    ):
        assert provision.main() == 1

    assert events[-1] == "authorize"
    assert events[0] == provision.LEGAL_AGREEMENT_PROMPT


def test_temporary_credentials_are_removed_after_flashing() -> None:
    temporary_paths: list[Path] = []

    def generate_nvs(_csv_path: Path, image_path: Path) -> None:
        image_path.write_bytes(b"test image")
        temporary_paths.append(image_path)

    def flash_nvs(image_path: Path) -> None:
        assert image_path.is_file()

    output = StringIO()
    with (
        patch("builtins.input", return_value="I AGREE"),
        patch.object(provision, "prompt_value", side_effect=["Test WiFi", "password", "client-id"]),
        patch.object(provision, "authorize", return_value="refresh-token"),
        patch.object(provision, "generate_nvs", side_effect=generate_nvs),
        patch.object(provision, "flash_nvs", side_effect=flash_nvs),
        redirect_stdout(output),
    ):
        assert provision.main() == 0

    assert "refresh-token" not in output.getvalue()
    assert temporary_paths
    for image_path in temporary_paths:
        assert not image_path.exists()
        assert not image_path.with_name("credentials.csv").exists()


def main() -> int:
    test_nvs_namespace_matches_the_firmware()
    test_written_csv_uses_the_firmware_nvs_namespace()
    test_legal_documents_are_available()
    test_refusal_stops_before_authorization()
    test_acceptance_starts_authorization_after_consent()
    test_temporary_credentials_are_removed_after_flashing()
    print("Provisioning consent checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
