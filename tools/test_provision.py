#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mark Phelps
# SPDX-License-Identifier: Apache-2.0

"""Verify the legal-consent and temporary-file controls for provisioning."""

from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import provision


ROOT = Path(__file__).resolve().parent.parent


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
    test_legal_documents_are_available()
    test_refusal_stops_before_authorization()
    test_acceptance_starts_authorization_after_consent()
    test_temporary_credentials_are_removed_after_flashing()
    print("Provisioning consent checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
