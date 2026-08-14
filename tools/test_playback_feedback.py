#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mark Phelps
# SPDX-License-Identifier: Apache-2.0

"""Verify source-level playback feedback and Spotify rate-limit controls."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI_SOURCE = (ROOT / "firmware/main/ui/ui.c").read_text(encoding="utf-8")
SPOTIFY_SOURCE = (ROOT / "firmware/main/spotify/spotify.c").read_text(encoding="utf-8")


def function_body(source: str, name: str, next_name: str) -> str:
    start = source.index(f"static void {name}(")
    end = source.index(f"static void {next_name}(", start)
    return source[start:end]


def assert_rejected(old: str, new: str) -> None:
    mutated = SPOTIFY_SOURCE.replace(old, new, 1)
    assert mutated != SPOTIFY_SOURCE
    try:
        assert_rate_limit_policy(mutated)
    except (AssertionError, ValueError):
        return
    raise AssertionError(f"The rate-limit validator accepted mutation: {old!r}")


def assert_rate_limit_policy(source: str) -> None:
    client_start = source.index("static void client_task(")
    client_end = source.index("esp_err_t bop_spotify_start(", client_start)
    client = source[client_start:client_end]

    assert "#define PLAYING_POLL_INTERVAL_MS 10000" in source
    assert "#define IDLE_POLL_INTERVAL_MS 60000" in source
    assert "TickType_t poll_deadline = xTaskGetTickCount();" in client
    assert "wait_for_rate_limit_expiry();" in client
    assert "state.available && state.is_playing" in client
    assert "? pdMS_TO_TICKS(PLAYING_POLL_INTERVAL_MS)" in client
    assert ": pdMS_TO_TICKS(IDLE_POLL_INTERVAL_MS);" in client
    assert "poll_current_playback(context, &retry_after_seconds, &rate_limited);" in client
    assert "if (command_error != ESP_OK)" in client
    assert 'ESP_LOGW(TAG, "Command was not applied");' in client
    assert client.index("            command_pending = false;") < client.index("if (rate_limited)")

    assert source.count("if (status_code == 429)") == 3
    assert "Token refresh rate limit" in source
    assert (
        'if (status_code == 429) {\n        ESP_LOGW(TAG, "Spotify rate limit");\n        return ESP_ERR_TIMEOUT;\n    }\n    if (status_code == 204)'
        in source
    )
    assert (
        'if (status_code == 429) {\n        ESP_LOGW(TAG, "Spotify rate limit");\n        return ESP_ERR_TIMEOUT;\n    }\n    if (status_code < 200'
        in source
    )
    assert "#define RATE_LIMIT_FALLBACK_SECONDS 60" in source
    assert "retry_after_seconds > 0 ? retry_after_seconds : RATE_LIMIT_FALLBACK_SECONDS" in source
    assert "seconds <= INT64_MAX / 1000000" in source
    assert "start_rate_limit_cooldown(retry_after_seconds);" in client
    assert "submission = SPOTIFY_COMMAND_SUBMISSION_RATE_LIMITED;" in source
    assert "if (rate_limit_deadline_us - esp_timer_get_time() <= 0)" in source
    assert "publish_command_result(&pending_command, false, true, was_playing);" in client


def test_rate_limit_policy_rejects_regressions() -> None:
    assert_rate_limit_policy(SPOTIFY_SOURCE)

    assert_rejected(
        "TickType_t poll_deadline = xTaskGetTickCount();",
        "TickType_t poll_deadline = xTaskGetTickCount() + pdMS_TO_TICKS(10000);",
    )
    assert_rejected(
        "#define PLAYING_POLL_INTERVAL_MS 10000", "#define PLAYING_POLL_INTERVAL_MS 2000"
    )
    assert_rejected("#define IDLE_POLL_INTERVAL_MS 60000", "#define IDLE_POLL_INTERVAL_MS 2000")
    assert_rejected(
        "? pdMS_TO_TICKS(PLAYING_POLL_INTERVAL_MS)\n            : pdMS_TO_TICKS(IDLE_POLL_INTERVAL_MS);",
        "? pdMS_TO_TICKS(IDLE_POLL_INTERVAL_MS)\n            : pdMS_TO_TICKS(PLAYING_POLL_INTERVAL_MS);",
    )
    assert_rejected(
        "poll_current_playback(context, &retry_after_seconds, &rate_limited);",
        "/* accepted-command playback refresh removed */",
    )
    assert_rejected("Token refresh rate limit", "Token refresh removed")
    assert_rejected(
        'if (status_code == 429) {\n        ESP_LOGW(TAG, "Spotify rate limit");\n        return ESP_ERR_TIMEOUT;\n    }\n    if (status_code == 204)',
        'if (status_code == 430) {\n        ESP_LOGW(TAG, "Spotify rate limit");\n        return ESP_ERR_TIMEOUT;\n    }\n    if (status_code == 204)',
    )
    assert_rejected(
        'if (status_code == 429) {\n        ESP_LOGW(TAG, "Spotify rate limit");\n        return ESP_ERR_TIMEOUT;\n    }\n    if (status_code < 200',
        'if (status_code == 430) {\n        ESP_LOGW(TAG, "Spotify rate limit");\n        return ESP_ERR_TIMEOUT;\n    }\n    if (status_code < 200',
    )
    assert_rejected("wait_for_rate_limit_expiry();", "/* cooldown gate removed */")
    assert_rejected(
        "#define RATE_LIMIT_FALLBACK_SECONDS 60", "#define RATE_LIMIT_FALLBACK_SECONDS 1"
    )
    assert_rejected(
        "retry_after_seconds > 0 ? retry_after_seconds : RATE_LIMIT_FALLBACK_SECONDS",
        "retry_after_seconds > 0 ? retry_after_seconds : 1",
    )
    assert_rejected("seconds <= INT64_MAX / 1000000", "seconds <= 3600")
    assert_rejected("if (rate_limit_deadline_us - esp_timer_get_time() <= 0)", "if (true)")
    assert_rejected(
        "command_pending = false;\n            if (rate_limited)",
        "if (rate_limited)",
    )


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
    assert "if (result.command != SPOTIFY_COMMAND_TOGGLE || result.request_id == 0)" in results
    assert "if (result.accepted)" in results
    assert "else if (result.rate_limited)" in results
    assert "show_feedback(LV_SYMBOL_WARNING);" in results
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
    test_rate_limit_policy_rejects_regressions()
    test_feedback_uses_the_album_art_center_and_duration()
    test_feedback_requires_an_accepted_tap_request()
    test_spotify_branding_is_limited_to_attribution()
    print("Playback feedback checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
