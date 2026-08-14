#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mark Phelps
# SPDX-License-Identifier: Apache-2.0

"""Verify playback gestures, commands, volume, and Spotify rate-limit controls."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI_SOURCE = (ROOT / "firmware/main/ui/ui.c").read_text(encoding="utf-8")
SPOTIFY_SOURCE = (ROOT / "firmware/main/spotify/spotify.c").read_text(encoding="utf-8")
SPOTIFY_HEADER = (ROOT / "firmware/main/spotify/spotify.h").read_text(encoding="utf-8")


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
    assert 'ESP_LOGW(TAG, "Command was not applied; refreshing playback state");' in client
    assert "if (!request_completed)" in client
    assert client.index("            command_pending = false;") < client.index("if (rate_limited)")

    assert source.count("if (status_code == 429)") == 4
    assert '*rate_limited = true;\n        ESP_LOGW(TAG, "Token refresh rate limit")' in source
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
    assert "if (error != ESP_OK || *status_code != 401)" in source
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
    assert_rejected(
        '*rate_limited = true;\n        ESP_LOGW(TAG, "Token refresh rate limit")',
        '*rate_limited = false;\n        ESP_LOGW(TAG, "Token refresh rate limit")',
    )
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
    assert_rejected(
        "submission = SPOTIFY_COMMAND_SUBMISSION_RATE_LIMITED;",
        "submission = SPOTIFY_COMMAND_SUBMISSION_QUEUED;",
    )
    assert_rejected("if (rate_limit_deadline_us - esp_timer_get_time() <= 0)", "if (true)")
    assert_rejected(
        "if (error != ESP_OK || *status_code != 401)", "if (error != ESP_OK || *status_code != 429)"
    )
    assert_rejected(
        "command_pending = false;\n            if (rate_limited)",
        "if (rate_limited)",
    )


def assert_ui_rejected(old: str, new: str) -> None:
    mutated = UI_SOURCE.replace(old, new, 1)
    assert mutated != UI_SOURCE
    try:
        assert_tap_feedback_policy(mutated)
    except (AssertionError, ValueError):
        return
    raise AssertionError(f"The tap-feedback validator accepted mutation: {old!r}")


def assert_tap_feedback_policy(source: str) -> None:
    gesture_start = source.index("static bool send_gesture_command(")
    gesture_end = source.index("static void handle_tap(", gesture_start)
    gesture = source[gesture_start:gesture_end]
    results = function_body(source, "process_command_results", "ui_timer")
    timer_start = source.index("static void ui_timer(")
    timer_end = source.index("esp_err_t bop_ui_start(", timer_start)
    timer = source[timer_start:timer_end]
    swipe_start = source.index("static void animate_swipe(")
    swipe_end = source.index("static bool send_gesture_command(", swipe_start)
    swipe = source[swipe_start:swipe_end]

    assert "submission == SPOTIFY_COMMAND_SUBMISSION_QUEUED" in gesture
    assert "submission == SPOTIFY_COMMAND_SUBMISSION_RATE_LIMITED" in gesture
    assert "&& request_id != 0" in gesture
    assert "&& !volume_feedback_active()" in gesture
    assert gesture.count("show_feedback(LV_SYMBOL_WARNING);") == 1
    assert "if (volume_feedback_active())" in results
    assert "else if (result.rate_limited)" in results
    assert results.count("show_feedback(LV_SYMBOL_WARNING);") == 1
    assert "show_feedback(" not in swipe
    assert "process_command_results();" in timer


def test_tap_feedback_rejects_regressions() -> None:
    assert_tap_feedback_policy(UI_SOURCE)

    assert_ui_rejected(
        "if (submission == SPOTIFY_COMMAND_SUBMISSION_RATE_LIMITED\n"
        "        && request_id != 0\n"
        "        && !volume_feedback_active())",
        "if (false)",
    )
    assert_ui_rejected("else if (result.rate_limited)", "else if (false)")
    assert_ui_rejected("return true;", "show_feedback(LV_SYMBOL_WARNING);\n        return true;")
    assert_ui_rejected(
        "    return false;\n}",
        "    if (submission == SPOTIFY_COMMAND_SUBMISSION_QUEUE_FULL) {\n"
        "        show_feedback(LV_SYMBOL_WARNING);\n"
        "    }\n"
        "    return false;\n}",
    )
    assert_ui_rejected(
        "    return false;\n}",
        "    if (submission == SPOTIFY_COMMAND_SUBMISSION_NOT_READY) {\n"
        "        show_feedback(LV_SYMBOL_WARNING);\n"
        "    }\n"
        "    return false;\n}",
    )
    assert_ui_rejected(
        "static void animate_swipe(int direction)\n{",
        "static void animate_swipe(int direction)\n{\n    show_feedback(LV_SYMBOL_WARNING);",
    )
    assert_ui_rejected("process_command_results();", "/* command results disabled */")


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
    assert "if (volume_feedback_active())" in results
    assert "if (result.accepted)" in results
    assert "else if (result.rate_limited)" in results
    assert "show_feedback(LV_SYMBOL_WARNING);" in results
    assert "send_gesture_command(SPOTIFY_COMMAND_PREVIOUS, 0)" in UI_SOURCE
    assert "send_gesture_command(SPOTIFY_COMMAND_NEXT, 0)" in UI_SOURCE
    assert (
        "publish_command_result(&pending_command, command_error == ESP_OK, false, was_playing);"
        in SPOTIFY_SOURCE
    )


def test_volume_drag_contract() -> None:
    ui_enqueue_start = UI_SOURCE.index("static bool enqueue_volume_target(")
    calculate_start = UI_SOURCE.index("static uint8_t calculate_volume_target(")
    ui_enqueue = UI_SOURCE[ui_enqueue_start:calculate_start]
    calculate_end = UI_SOURCE.index("static void update_drag(", calculate_start)
    calculate = UI_SOURCE[calculate_start:calculate_end]
    drag_start = calculate_end
    drag_end = UI_SOURCE.index("static void finish_volume_update(", drag_start)
    drag = UI_SOURCE[drag_start:drag_end]
    finish_start = drag_end
    finish_end = UI_SOURCE.index("static void cancel_volume_gesture(", finish_start)
    finish = UI_SOURCE[finish_start:finish_end]
    cancel_start = finish_end
    cancel_end = UI_SOURCE.index("static void gesture_event(", cancel_start)
    cancel = UI_SOURCE[cancel_start:cancel_end]
    gesture_start = cancel_end
    gesture_end = UI_SOURCE.index("static void create_offline_indicator(", gesture_start)
    gesture = UI_SOURCE[gesture_start:gesture_end]
    pressed_start = gesture.index("if (code == LV_EVENT_PRESSED)")
    pressed_end = gesture.index("if (code == LV_EVENT_PRESSING", pressed_start)
    pressed = gesture[pressed_start:pressed_end]
    long_pressed_start = gesture.index("if (code == LV_EVENT_LONG_PRESSED")
    long_pressed_end = gesture.index("if (code != LV_EVENT_RELEASED", long_pressed_start)
    long_pressed = gesture[long_pressed_start:long_pressed_end]
    movement_block = """if (horizontal_distance > TAP_THRESHOLD || vertical_distance > TAP_THRESHOLD) {
        ui.press_moved = true;
    }"""
    movement_start = drag.index(movement_block)
    axis_selection = drag.index("if (ui.gesture_axis == GESTURE_AXIS_NONE)")
    availability_check = drag.index("else if (ui.volume_drag_available")
    non_vertical_return = drag.index("if (ui.gesture_axis != GESTURE_AXIS_VERTICAL)")
    update_track_start = UI_SOURCE.index("static void update_track(")
    update_track_end = UI_SOURCE.index("static void format_time(", update_track_start)
    update_track = UI_SOURCE[update_track_start:update_track_end]
    acknowledgement = """if (ui.volume_target_awaiting_snapshot
        && state->volume_command_generation == ui.volume_snapshot_generation) {
        ui.volume_target_awaiting_snapshot = false;
    }"""
    enqueue_call = ui_enqueue.index("if (!bop_spotify_enqueue_volume(")
    enqueue_failure = ui_enqueue.index("return false;", enqueue_call)
    generation_capture = ui_enqueue.index("bop_spotify_get_volume_request_generation()")
    enqueue_success = ui_enqueue.index("return true;", generation_capture)
    enqueue_success_block = """if (!bop_spotify_enqueue_volume(ui.volume_target_percent)) {
        ESP_LOGW(TAG, "Spotify volume target was not queued");
        return false;
    }
    ui.volume_last_sent_percent = ui.volume_target_percent;
    ui.volume_last_sent_valid = true;
    ui.volume_snapshot_generation = bop_spotify_get_volume_request_generation();
    ui.volume_target_awaiting_snapshot = true;
    return true;
}"""

    assert "#define VOLUME_UPDATE_INTERVAL_US 150000" in UI_SOURCE
    assert "#define VOLUME_FINAL_DELAY_US 400000" in UI_SOURCE
    assert "#define VOLUME_CURVE_EXPONENT 1.8f" in UI_SOURCE
    assert "#define FEEDBACK_DURATION_MS 750" in UI_SOURCE
    assert (
        "lv_obj_add_event_cb(ui.gesture_layer, gesture_event, LV_EVENT_PRESSING, NULL);"
        in UI_SOURCE
    )
    assert "ui.state.available" in gesture
    assert "ui.state.volume_available" in gesture
    assert "bool press_moved;" in UI_SOURCE
    assert "ui.press_moved = false;" in pressed
    assert movement_block in drag
    assert movement_start < axis_selection < availability_check < non_vertical_return
    assert "&& !ui.press_moved" in long_pressed
    assert "vertical_distance >= SWIPE_THRESHOLD" in drag
    assert "horizontal_distance >= SWIPE_THRESHOLD" in drag
    assert "GESTURE_AXIS_VERTICAL" in drag
    assert "GESTURE_AXIS_HORIZONTAL" in drag
    assert "(float)ART_SIZE" in calculate
    assert "powf(normalized, VOLUME_CURVE_EXPONENT)" in calculate
    assert "target < 0" in calculate
    assert "target > 100" in calculate
    assert "volume_target_percent == ui.volume_last_sent_percent" in UI_SOURCE
    assert "now - ui.volume_last_attempt_us >= VOLUME_UPDATE_INTERVAL_US" in UI_SOURCE
    assert "ui.volume_last_attempt_us = now;" in UI_SOURCE
    assert "ui.volume_target_awaiting_snapshot = true;" in ui_enqueue
    assert enqueue_call < enqueue_failure < generation_capture < enqueue_success
    assert enqueue_success_block in ui_enqueue
    assert (
        "ui.volume_snapshot_generation = bop_spotify_get_volume_request_generation();" in ui_enqueue
    )
    assert acknowledgement in update_track
    assert "use_local_target = ui.volume_final_pending" in gesture
    assert "|| ui.volume_target_awaiting_snapshot" in gesture
    assert "? ui.volume_target_percent" in gesture
    assert "volume_enqueue_due(now)" in drag
    assert "ui.volume_final_deadline_us = now + VOLUME_FINAL_DELAY_US" in drag
    assert "ui.volume_final_deadline_us = esp_timer_get_time() + VOLUME_FINAL_DELAY_US" in gesture
    assert "now < ui.volume_final_deadline_us" in finish
    assert "if (!enqueue_volume_target(now))" in finish
    assert "now + VOLUME_UPDATE_INTERVAL_US" in finish
    assert "if (ui.press_active)" in finish
    assert "FEEDBACK_DURATION_MS * 1000" in finish
    assert "show_feedback(text);" in finish
    assert "volume_final_pending = false" not in pressed
    assert "point->x == ui.volume_last_point.x" in drag
    assert "target_changed || vertical_started" in drag
    assert "ui.gesture_axis == GESTURE_AXIS_VERTICAL" in cancel
    assert "ui.gesture_axis == GESTURE_AXIS_NONE" in cancel
    assert "if (vertical_press || available_press)" in cancel
    assert "if (!state->available || !state->volume_available)" in UI_SOURCE
    assert '"%u%%"' in UI_SOURCE
    assert "SPOTIFY_COMMAND_PREVIOUS" in gesture
    assert "SPOTIFY_COMMAND_NEXT" in gesture
    assert "handle_tap();" in gesture
    assert "show_attribution();" in gesture

    def target(start: int, vertical: int) -> int:
        change = int((abs(vertical) / 368) ** 1.8 * 100 + 0.5)
        value = start + change if vertical < 0 else start - change
        return max(0, min(100, value))

    assert target(50, -48) == 53
    assert target(50, -96) == 59
    assert target(50, -184) == 79
    assert target(50, 240) == 4
    assert target(79, -48) == 82


def test_spotify_volume_command_contract() -> None:
    parser_start = SPOTIFY_SOURCE.index("static esp_err_t parse_playback(")
    parser_end = SPOTIFY_SOURCE.index("static esp_err_t poll_current_playback(")
    parser = SPOTIFY_SOURCE[parser_start:parser_end]
    poll_start = parser_end
    poll_end = SPOTIFY_SOURCE.index("static const char *command_name(", poll_start)
    poll = SPOTIFY_SOURCE[poll_start:poll_end]
    empty_start = poll.index("if (status_code == 204)")
    empty_end = poll.index("if (status_code != 200)", empty_start)
    empty_poll = poll[empty_start:empty_end]
    null_item_start = parser.index("if (cJSON_IsNull(item))")
    null_item_end = parser.index("cJSON *title", null_item_start)
    null_item = parser[null_item_start:null_item_end]
    sender_start = SPOTIFY_SOURCE.index("static esp_err_t send_volume(")
    sender_end = SPOTIFY_SOURCE.index("static void publish_command_result(")
    sender = SPOTIFY_SOURCE[sender_start:sender_end]
    task_start = SPOTIFY_SOURCE.index("static void client_task(")
    task_end = SPOTIFY_SOURCE.index("esp_err_t bop_spotify_start(")
    client_task = SPOTIFY_SOURCE[task_start:task_end]
    volume_task_start = client_task.index("if (volume_pending)")
    volume_task_end = client_task.index("if (!request_completed)", volume_task_start)
    volume_task = client_task[volume_task_start:volume_task_end]
    retry_start = volume_task.index("if (rate_limited)")
    generation_set = volume_task.index(
        "context->volume_command_generation = pending_volume.generation"
    )
    failure_check = volume_task.index("if (volume_error != ESP_OK)")
    parse_call = poll.index("error = parse_playback(context->response.data, &next)")
    parse_success = poll.index("if (error == ESP_OK)", parse_call)
    parsed_generation = poll.index(
        "next.volume_command_generation = context->volume_command_generation"
    )
    parsed_publish = poll.index("publish_state(&next)")
    parsed_success_block = """if (error == ESP_OK) {
        next.volume_command_generation = context->volume_command_generation;
        publish_state(&next);
    } else {
        ESP_LOGE(TAG, "Playback response is invalid");
    }"""
    enqueue_start = SPOTIFY_SOURCE.index("bool bop_spotify_enqueue_volume(")
    enqueue_end = SPOTIFY_SOURCE.index("bool bop_spotify_get_command_result(")
    enqueue = SPOTIFY_SOURCE[enqueue_start:enqueue_end]

    assert SPOTIFY_SOURCE.count('"https://api.spotify.com/v1/me/player"') == 2
    assert "/v1/me/player/currently-playing" not in SPOTIFY_SOURCE
    assert "uint8_t volume_percent;" in SPOTIFY_HEADER
    assert "bool volume_available;" in SPOTIFY_HEADER
    assert "uint32_t volume_command_generation;" in SPOTIFY_HEADER
    assert "SPOTIFY_COMMAND_SET_VOLUME," in SPOTIFY_HEADER
    assert "bool bop_spotify_enqueue_volume(uint8_t volume_percent);" in SPOTIFY_HEADER
    assert "uint32_t bop_spotify_get_volume_request_generation(void);" in SPOTIFY_HEADER
    assert 'cJSON_GetObjectItemCaseSensitive(root, "device")' in parser
    assert 'cJSON_GetObjectItemCaseSensitive(device, "volume_percent")' in parser
    assert '"supports_volume"' not in parser
    assert "cJSON_IsObject(device)" in parser
    assert "cJSON_IsNumber(volume)" in parser
    assert "volume->valuedouble >= 0.0" in parser
    assert "volume->valuedouble <= 100.0" in parser
    assert "volume->valuedouble == (double)volume->valueint" in parser
    assert parser.index('cJSON_GetObjectItemCaseSensitive(root, "device")') < parser.index(
        "if (cJSON_IsNull(item))"
    )
    assert "playback_state_t" not in null_item
    assert "left->volume_percent == right->volume_percent" in SPOTIFY_SOURCE
    assert "left->volume_available == right->volume_available" in SPOTIFY_SOURCE
    assert "left->volume_command_generation == right->volume_command_generation" in SPOTIFY_SOURCE
    assert (
        "QueueHandle_t volumes = xQueueCreate(1, sizeof(spotify_volume_request_t));"
        in SPOTIFY_SOURCE
    )
    assert "volume_queue = volumes;" in SPOTIFY_SOURCE
    assert "volume_percent > 100" in enqueue
    assert "!state.volume_available" in enqueue
    assert "generation = volume_request_generation + 1" in enqueue
    assert "if (generation == 0)" in enqueue
    assert "generation = 1;" in enqueue
    assert "xQueueOverwrite(volume_queue, &request)" in enqueue
    assert enqueue.index("xQueueOverwrite(volume_queue, &request)") < enqueue.index(
        "volume_request_generation = generation"
    )
    assert "return volume_request_generation;" in enqueue
    assert "xTaskNotifyGive(client_task_handle);" in enqueue
    assert "/v1/me/player/volume?volume_percent=%u" in sender
    assert "HTTP_METHOD_PUT" in sender
    assert "status_code != 204" in sender
    assert "!state.volume_available" in sender
    assert "publish_command_result" not in sender
    assert client_task.index("send_command(") < client_task.index("send_volume(")
    assert client_task.index("send_volume(") < client_task.index("poll_current_playback(")
    assert retry_start < generation_set < failure_check
    assert "continue;" in volume_task[retry_start:generation_set]
    assert "volume_command_generation" not in volume_task[retry_start:generation_set]
    assert generation_set < volume_task.index("volume_pending = false")
    assert client_task.index(
        "context->volume_command_generation = pending_volume.generation"
    ) < client_task.index("poll_current_playback(")
    assert parse_call < parse_success < parsed_generation < parsed_publish
    assert parsed_success_block in poll
    assert ".volume_command_generation = context->volume_command_generation" in empty_poll
    assert "bool request_completed = false;" in client_task
    assert client_task.count("request_completed = true;") == 2
    assert "if (!request_completed)" in client_task

    def snapshot_acknowledges(awaited_generation: int, snapshot_generation: int) -> bool:
        return snapshot_generation == awaited_generation

    assert not snapshot_acknowledges(2, 1)
    assert snapshot_acknowledges(2, 2)


def test_spotify_branding_is_limited_to_attribution() -> None:
    assert "make_spotify_mark(ui.screen" not in UI_SOURCE
    assert UI_SOURCE.count("make_spotify_mark(") == 2
    assert "make_spotify_mark(ui.attribution, 36)" in UI_SOURCE


def main() -> int:
    test_rate_limit_policy_rejects_regressions()
    test_tap_feedback_rejects_regressions()
    test_feedback_uses_the_album_art_center_and_duration()
    test_feedback_requires_an_accepted_tap_request()
    test_volume_drag_contract()
    test_spotify_volume_command_contract()
    test_spotify_branding_is_limited_to_attribution()
    print("Playback feedback checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
