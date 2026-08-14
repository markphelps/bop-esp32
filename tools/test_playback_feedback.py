#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mark Phelps
# SPDX-License-Identifier: Apache-2.0

"""Verify source-level controls for accepted playback feedback."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI_SOURCE = (ROOT / "firmware/main/ui/ui.c").read_text(encoding="utf-8")
SPOTIFY_SOURCE = (ROOT / "firmware/main/spotify/spotify.c").read_text(encoding="utf-8")


def function_body(source: str, name: str, next_name: str) -> str:
    start = source.index(f"static void {name}(")
    end = source.index(f"static void {next_name}(", start)
    return source[start:end]


def test_feedback_uses_the_album_art_center_and_duration() -> None:
    feedback = function_body(UI_SOURCE, "create_feedback_layer", "show_playing")

    assert "#define FEEDBACK_SIZE 96" in UI_SOURCE
    assert "#define FEEDBACK_DURATION_MS 750" in UI_SOURCE
    assert "lv_obj_set_size(ui.feedback, FEEDBACK_SIZE, FEEDBACK_SIZE);" in feedback
    assert (
        "lv_obj_align(ui.feedback, LV_ALIGN_TOP_MID, 0, (ART_SIZE - FEEDBACK_SIZE) / 2);"
        in feedback
    )
    assert "lv_obj_set_style_radius(ui.feedback, LV_RADIUS_CIRCLE, 0);" in feedback
    assert "lv_anim_set_delay(&animation, FEEDBACK_DURATION_MS);" in UI_SOURCE


def test_feedback_requires_an_accepted_tap_request() -> None:
    tap = function_body(UI_SOURCE, "handle_tap", "restore_normal_brightness")
    results = function_body(UI_SOURCE, "process_command_results", "ui_timer")

    assert "send_gesture_command(SPOTIFY_COMMAND_TOGGLE, request_id)" in tap
    assert "show_feedback(" not in tap
    assert "if (result.command == SPOTIFY_COMMAND_TOGGLE" in results
    assert "&& result.request_id != 0" in results
    assert "&& result.accepted)" in results
    assert "send_gesture_command(SPOTIFY_COMMAND_PREVIOUS, 0)" in UI_SOURCE
    assert "send_gesture_command(SPOTIFY_COMMAND_NEXT, 0)" in UI_SOURCE
    assert (
        "publish_command_result(&pending_command, command_error == ESP_OK, false, was_playing);"
        in SPOTIFY_SOURCE
    )


def test_spotify_branding_is_limited_to_attribution() -> None:
    assert "make_spotify_mark(ui.screen" not in UI_SOURCE
    assert UI_SOURCE.count("make_spotify_mark(") == 2
    assert "make_spotify_mark(ui.attribution, 36)" in UI_SOURCE


def main() -> int:
    test_feedback_uses_the_album_art_center_and_duration()
    test_feedback_requires_an_accepted_tap_request()
    test_spotify_branding_is_limited_to_attribution()
    print("Playback feedback checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
