// SPDX-FileCopyrightText: 2026 Mark Phelps
// SPDX-License-Identifier: Apache-2.0

#include "ui.h"

#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "bsp/display.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "lvgl.h"
#include "misc/cache/instance/lv_image_cache.h"
#include "art.h"
#include "../power.h"
#include "../spotify/spotify.h"
#include "../wifi.h"

LV_FONT_DECLARE(lv_font_montserrat_bold_24);

#define SCREEN_WIDTH 368
#define SCREEN_HEIGHT 448
#define ART_SIZE 368
#define INFO_TOP 368
#define UI_TIMER_MS 250
#define SWIPE_THRESHOLD 48
#define TAP_THRESHOLD 20
#define UI_TASK_CORE 1
#define NORMAL_BRIGHTNESS 85
#define DIM_BRIGHTNESS 10
#define BATTERY_DIM_TIMEOUT_US (30LL * 1000000)
#define ART_CORNER_RADIUS 4
#define FEEDBACK_SIZE 96
#define FEEDBACK_DURATION_MS 750
#define ATTRIBUTION_QR_SIZE 208
#define ATTRIBUTION_URL_CAPACITY (sizeof("https://open.spotify.com/track/") + BOP_TRACK_ID_CAPACITY)

static const char *TAG = "bop_ui";

typedef struct {
    lv_obj_t *screen;
    lv_obj_t *band;
    lv_obj_t *art_placeholder;
    lv_obj_t *art_image;
    lv_obj_t *title;
    lv_obj_t *artist;
    lv_obj_t *progress;
    lv_obj_t *elapsed;
    lv_obj_t *total;
    lv_obj_t *idle;
    lv_obj_t *gesture_layer;
    lv_obj_t *offline;
    lv_obj_t *feedback;
    lv_obj_t *feedback_glyph;
    lv_obj_t *attribution;
    lv_obj_t *attribution_qr;
    QueueHandle_t art_queue;
    bop_album_art_t *active_art;
    playback_state_t state;
    int64_t state_received_us;
    uint32_t rendered_change_counter;
    uint32_t optimistic_change_counter;
    uint32_t next_feedback_request_id;
    int64_t optimistic_until_us;
    bool optimistic_toggle;
    bool attribution_visible;
    bool battery_dimmed;
    bool press_active;
    lv_point_t press_point;
    int64_t last_touch_us;
} ui_context_t;

static ui_context_t ui;

static lv_obj_t *make_label(
    lv_obj_t *parent,
    const lv_font_t *font,
    lv_color_t color,
    int32_t x,
    int32_t y,
    int32_t width,
    int32_t height)
{
    lv_obj_t *label = lv_label_create(parent);
    lv_obj_set_pos(label, x, y);
    lv_obj_set_size(label, width, height);
    lv_obj_set_style_text_font(label, font, 0);
    lv_obj_set_style_text_color(label, color, 0);
    lv_obj_set_style_pad_all(label, 0, 0);
    return label;
}

static lv_obj_t *make_logo_line(lv_obj_t *parent, int32_t y, int32_t width)
{
    lv_obj_t *line = lv_obj_create(parent);
    lv_obj_remove_style_all(line);
    lv_obj_set_size(line, width, 5);
    lv_obj_align(line, LV_ALIGN_CENTER, 0, y);
    lv_obj_set_style_bg_color(line, lv_color_hex(0x071107), 0);
    lv_obj_set_style_bg_opa(line, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(line, LV_RADIUS_CIRCLE, 0);
    return line;
}

static lv_obj_t *make_spotify_mark(lv_obj_t *parent, int32_t size)
{
    lv_obj_t *mark = lv_obj_create(parent);
    lv_obj_remove_style_all(mark);
    lv_obj_set_size(mark, size, size);
    lv_obj_set_style_bg_color(mark, lv_color_hex(0x1ED760), 0);
    lv_obj_set_style_bg_opa(mark, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(mark, LV_RADIUS_CIRCLE, 0);
    lv_obj_remove_flag(mark, LV_OBJ_FLAG_SCROLLABLE);

    int32_t line_height = size / 7;
    int32_t line_width = size * 3 / 4;
    for (int32_t index = 0; index < 3; ++index) {
        lv_obj_t *line = lv_obj_create(mark);
        lv_obj_remove_style_all(line);
        lv_obj_set_size(line, line_width - index * size / 10, line_height);
        lv_obj_align(line, LV_ALIGN_CENTER, 0, (index - 1) * size / 4);
        lv_obj_set_style_bg_color(line, lv_color_hex(0x000000), 0);
        lv_obj_set_style_bg_opa(line, LV_OPA_COVER, 0);
        lv_obj_set_style_radius(line, LV_RADIUS_CIRCLE, 0);
    }
    return mark;
}

static void create_idle_state(void)
{
    ui.idle = lv_obj_create(ui.screen);
    lv_obj_remove_style_all(ui.idle);
    lv_obj_set_size(ui.idle, SCREEN_WIDTH, SCREEN_HEIGHT);
    lv_obj_set_pos(ui.idle, 0, 0);
    lv_obj_remove_flag(ui.idle, LV_OBJ_FLAG_SCROLLABLE);

    lv_obj_t *logo = lv_obj_create(ui.idle);
    lv_obj_remove_style_all(logo);
    lv_obj_set_size(logo, 88, 88);
    lv_obj_align(logo, LV_ALIGN_CENTER, 0, -42);
    lv_obj_set_style_bg_color(logo, lv_color_hex(0x1ED760), 0);
    lv_obj_set_style_bg_opa(logo, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(logo, LV_RADIUS_CIRCLE, 0);
    make_logo_line(logo, -18, 56);
    make_logo_line(logo, 0, 50);
    make_logo_line(logo, 18, 42);

    lv_obj_t *message = make_label(
        ui.idle,
        &lv_font_montserrat_24,
        lv_color_hex(0xF4F4F4),
        24,
        260,
        320,
        28);
    lv_label_set_text(message, "nothing playing");
    lv_obj_set_style_text_align(message, LV_TEXT_ALIGN_CENTER, 0);

    lv_obj_t *hint = make_label(
        ui.idle,
        &lv_font_montserrat_14,
        lv_color_hex(0xA7A7A7),
        24,
        296,
        320,
        22);
    lv_label_set_text(hint, "Start Spotify on any device");
    lv_obj_set_style_text_align(hint, LV_TEXT_ALIGN_CENTER, 0);
}

static void create_playing_state(void)
{
    ui.band = lv_obj_create(ui.screen);
    lv_obj_remove_style_all(ui.band);
    lv_obj_set_pos(ui.band, 0, INFO_TOP);
    lv_obj_set_size(ui.band, SCREEN_WIDTH, SCREEN_HEIGHT - INFO_TOP);
    lv_obj_set_style_bg_color(ui.band, lv_color_hex(0x181818), 0);
    lv_obj_set_style_bg_grad_color(ui.band, lv_color_hex(0x080808), 0);
    lv_obj_set_style_bg_grad_dir(ui.band, LV_GRAD_DIR_VER, 0);
    lv_obj_set_style_bg_opa(ui.band, LV_OPA_COVER, 0);

    ui.art_placeholder = lv_obj_create(ui.screen);
    lv_obj_remove_style_all(ui.art_placeholder);
    lv_obj_set_pos(ui.art_placeholder, 0, 0);
    lv_obj_set_size(ui.art_placeholder, ART_SIZE, ART_SIZE);
    lv_obj_set_style_bg_color(ui.art_placeholder, lv_color_hex(0x181818), 0);
    lv_obj_set_style_bg_opa(ui.art_placeholder, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(ui.art_placeholder, ART_CORNER_RADIUS, 0);
    lv_obj_set_style_clip_corner(ui.art_placeholder, true, 0);

    ui.art_image = lv_image_create(ui.screen);
    lv_obj_set_style_radius(ui.art_image, ART_CORNER_RADIUS, 0);
    lv_obj_set_style_clip_corner(ui.art_image, true, 0);
    lv_obj_add_flag(ui.art_image, LV_OBJ_FLAG_HIDDEN);

    ui.title = make_label(
        ui.screen,
        &lv_font_montserrat_bold_24,
        lv_color_hex(0xFFFFFF),
        12,
        INFO_TOP,
        272,
        32);
    lv_label_set_long_mode(ui.title, LV_LABEL_LONG_SCROLL_CIRCULAR);

    ui.artist = make_label(
        ui.screen,
        &lv_font_montserrat_14,
        lv_color_hex(0xB3B3B3),
        12,
        INFO_TOP + 32,
        272,
        17);
    lv_label_set_long_mode(ui.artist, LV_LABEL_LONG_SCROLL_CIRCULAR);

    ui.progress = lv_bar_create(ui.screen);
    lv_obj_set_pos(ui.progress, 16, INFO_TOP + 51);
    lv_obj_set_size(ui.progress, 336, 3);
    lv_bar_set_range(ui.progress, 0, 1000);
    lv_obj_set_style_radius(ui.progress, LV_RADIUS_CIRCLE, LV_PART_MAIN);
    lv_obj_set_style_radius(ui.progress, LV_RADIUS_CIRCLE, LV_PART_INDICATOR);
    lv_obj_set_style_bg_color(ui.progress, lv_color_hex(0x5E5E5E), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(ui.progress, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_bg_color(ui.progress, lv_color_hex(0x1ED760), LV_PART_INDICATOR);
    lv_obj_set_style_bg_opa(ui.progress, LV_OPA_COVER, LV_PART_INDICATOR);

    ui.elapsed = make_label(
        ui.screen,
        &lv_font_montserrat_14,
        lv_color_hex(0xA7A7A7),
        24,
        INFO_TOP + 55,
        88,
        17);
    ui.total = make_label(
        ui.screen,
        &lv_font_montserrat_14,
        lv_color_hex(0xA7A7A7),
        256,
        INFO_TOP + 55,
        88,
        17);
    lv_obj_set_style_text_align(ui.total, LV_TEXT_ALIGN_RIGHT, 0);

}

static void feedback_opacity(void *object, int32_t value)
{
    lv_obj_set_style_opa(object, value, 0);
}

static void hide_feedback(lv_anim_t *animation)
{
    lv_obj_add_flag(animation->var, LV_OBJ_FLAG_HIDDEN);
}

static void show_feedback(const char *glyph)
{
    lv_anim_delete(ui.feedback, feedback_opacity);
    lv_label_set_text(ui.feedback_glyph, glyph);
    lv_obj_remove_flag(ui.feedback, LV_OBJ_FLAG_HIDDEN);
    lv_obj_set_style_opa(ui.feedback, LV_OPA_COVER, 0);

    lv_anim_t animation;
    lv_anim_init(&animation);
    lv_anim_set_var(&animation, ui.feedback);
    lv_anim_set_exec_cb(&animation, feedback_opacity);
    lv_anim_set_values(&animation, LV_OPA_COVER, LV_OPA_COVER);
    lv_anim_set_duration(&animation, 1);
    lv_anim_set_delay(&animation, FEEDBACK_DURATION_MS);
    lv_anim_set_completed_cb(&animation, hide_feedback);
    lv_anim_start(&animation);
}

static void hide_attribution(lv_event_t *event)
{
    (void)event;
    lv_obj_add_flag(ui.attribution, LV_OBJ_FLAG_HIDDEN);
    ui.attribution_visible = false;
}

static void show_attribution(void)
{
    if (!ui.state.available || ui.state.track_id[0] == '\0') {
        return;
    }

    char url[ATTRIBUTION_URL_CAPACITY];
    int written = snprintf(
        url,
        sizeof(url),
        "https://open.spotify.com/track/%s",
        ui.state.track_id);
    if (written < 0 || (size_t)written >= sizeof(url)) {
        ESP_LOGW(TAG, "Track URL is too long for attribution");
        return;
    }
    if (lv_qrcode_update(ui.attribution_qr, url, strlen(url)) != LV_RESULT_OK) {
        ESP_LOGW(TAG, "Could not create Spotify attribution QR code");
        return;
    }

    ui.attribution_visible = true;
    lv_obj_remove_flag(ui.attribution, LV_OBJ_FLAG_HIDDEN);
    lv_obj_move_foreground(ui.attribution);
}

static void create_attribution_screen(void)
{
    ui.attribution = lv_obj_create(ui.screen);
    lv_obj_remove_style_all(ui.attribution);
    lv_obj_set_size(ui.attribution, SCREEN_WIDTH, SCREEN_HEIGHT);
    lv_obj_set_pos(ui.attribution, 0, 0);
    lv_obj_set_style_bg_color(ui.attribution, lv_color_hex(0x080808), 0);
    lv_obj_set_style_bg_opa(ui.attribution, LV_OPA_COVER, 0);
    lv_obj_add_flag(ui.attribution, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_remove_flag(ui.attribution, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_event_cb(ui.attribution, hide_attribution, LV_EVENT_CLICKED, NULL);

    lv_obj_t *mark = make_spotify_mark(ui.attribution, 36);
    lv_obj_align(mark, LV_ALIGN_TOP_MID, 0, 26);
    lv_obj_t *heading = make_label(
        ui.attribution,
        &lv_font_montserrat_bold_24,
        lv_color_hex(0xFFFFFF),
        0,
        70,
        SCREEN_WIDTH,
        28);
    lv_label_set_text(heading, "Spotify");
    lv_obj_set_style_text_align(heading, LV_TEXT_ALIGN_CENTER, 0);

    ui.attribution_qr = lv_qrcode_create(ui.attribution);
    lv_qrcode_set_size(ui.attribution_qr, ATTRIBUTION_QR_SIZE);
    lv_qrcode_set_dark_color(ui.attribution_qr, lv_color_hex(0x000000));
    lv_qrcode_set_light_color(ui.attribution_qr, lv_color_hex(0xFFFFFF));
    lv_obj_align(ui.attribution_qr, LV_ALIGN_TOP_MID, 0, 112);

    lv_obj_t *caption = make_label(
        ui.attribution,
        &lv_font_montserrat_14,
        lv_color_hex(0xB3B3B3),
        24,
        336,
        320,
        18);
    lv_label_set_text(caption, "Scan to open this track in Spotify");
    lv_obj_set_style_text_align(caption, LV_TEXT_ALIGN_CENTER, 0);

    lv_obj_t *hint = make_label(
        ui.attribution,
        &lv_font_montserrat_14,
        lv_color_hex(0xFFFFFF),
        24,
        378,
        320,
        18);
    lv_label_set_text(hint, "Tap to return");
    lv_obj_set_style_text_align(hint, LV_TEXT_ALIGN_CENTER, 0);

    lv_obj_add_flag(ui.attribution, LV_OBJ_FLAG_HIDDEN);
}

static void set_art_x(void *object, int32_t value)
{
    lv_obj_set_x(object, value);
}

static void animate_swipe(int direction)
{
    lv_obj_t *target = lv_obj_has_flag(ui.art_image, LV_OBJ_FLAG_HIDDEN)
        ? ui.art_placeholder
        : ui.art_image;
    int32_t base = lv_obj_get_x(target);
    lv_anim_delete(target, set_art_x);
    lv_anim_t animation;
    lv_anim_init(&animation);
    lv_anim_set_var(&animation, target);
    lv_anim_set_exec_cb(&animation, set_art_x);
    lv_anim_set_values(&animation, base, base + direction * 38);
    lv_anim_set_duration(&animation, 110);
    lv_anim_set_playback_duration(&animation, 190);
    lv_anim_set_path_cb(&animation, lv_anim_path_ease_out);
    lv_anim_start(&animation);
}

static bool send_gesture_command(spotify_command_t command, uint32_t request_id)
{
    if (bop_spotify_enqueue_command(command, request_id)) {
        return true;
    }
    ESP_LOGW(TAG, "Spotify command queue is full");
    return false;
}

static void handle_tap(void)
{
    bool was_playing = ui.state.is_playing;
    uint32_t request_id = ++ui.next_feedback_request_id;
    if (request_id == 0) {
        request_id = ++ui.next_feedback_request_id;
    }
    if (!send_gesture_command(SPOTIFY_COMMAND_TOGGLE, request_id)) {
        return;
    }

    int64_t now = esp_timer_get_time();
    if (was_playing && ui.state.duration_ms > 0) {
        int64_t elapsed_us = now - ui.state_received_us;
        if (elapsed_us > 0) {
            uint64_t progress = ui.state.progress_ms + (uint64_t)elapsed_us / 1000;
            ui.state.progress_ms = progress > ui.state.duration_ms
                ? ui.state.duration_ms
                : (uint32_t)progress;
        }
    }
    ui.state.is_playing = !was_playing;
    ui.state_received_us = now;
    ui.optimistic_toggle = true;
    ui.optimistic_change_counter = ui.rendered_change_counter;
    ui.optimistic_until_us = ui.state_received_us + 2000000;
}

static void restore_normal_brightness(void)
{
    if (!ui.battery_dimmed) {
        return;
    }
    esp_err_t error = bsp_display_brightness_set(NORMAL_BRIGHTNESS);
    if (error == ESP_OK) {
        ui.battery_dimmed = false;
        ESP_LOGI(TAG, "Restored display brightness");
    } else {
        ESP_LOGW(TAG, "Could not restore brightness: %s", esp_err_to_name(error));
    }
}

static void record_touch(void)
{
    ui.last_touch_us = esp_timer_get_time();
    restore_normal_brightness();
}

static void gesture_event(lv_event_t *event)
{
    lv_event_code_t code = lv_event_get_code(event);
    lv_indev_t *input = lv_indev_active();
    if (input == NULL) {
        return;
    }
    if (code == LV_EVENT_PRESSED) {
        record_touch();
        lv_indev_get_point(input, &ui.press_point);
        ui.press_active = true;
        return;
    }
    if (code == LV_EVENT_LONG_PRESSED && ui.press_active) {
        ui.press_active = false;
        show_attribution();
        return;
    }
    if (code != LV_EVENT_RELEASED || !ui.press_active) {
        return;
    }

    lv_point_t released;
    lv_indev_get_point(input, &released);
    ui.press_active = false;
    int32_t horizontal = released.x - ui.press_point.x;
    int32_t vertical = released.y - ui.press_point.y;
    int32_t horizontal_distance = abs(horizontal);
    int32_t vertical_distance = abs(vertical);
    if (horizontal_distance >= SWIPE_THRESHOLD
        && horizontal_distance > vertical_distance * 3 / 2) {
        if (horizontal < 0) {
            animate_swipe(-1);
            send_gesture_command(SPOTIFY_COMMAND_PREVIOUS, 0);
        } else {
            animate_swipe(1);
            send_gesture_command(SPOTIFY_COMMAND_NEXT, 0);
        }
    } else if (horizontal_distance <= TAP_THRESHOLD && vertical_distance <= TAP_THRESHOLD) {
        handle_tap();
    }
}

static void create_offline_indicator(void)
{
    ui.offline = make_label(
        ui.screen,
        &lv_font_montserrat_14,
        lv_color_hex(0xB3B3B3),
        276,
        8,
        80,
        18);
    lv_label_set_text(ui.offline, "offline");
    lv_obj_set_style_text_align(ui.offline, LV_TEXT_ALIGN_RIGHT, 0);
    lv_obj_set_style_bg_color(ui.offline, lv_color_hex(0x080808), 0);
    lv_obj_set_style_bg_opa(ui.offline, LV_OPA_70, 0);
    lv_obj_set_style_pad_hor(ui.offline, 4, 0);
    lv_obj_add_flag(ui.offline, LV_OBJ_FLAG_HIDDEN);
}

static void create_feedback_layer(void)
{
    ui.feedback = lv_obj_create(ui.screen);
    lv_obj_remove_style_all(ui.feedback);
    lv_obj_set_size(ui.feedback, FEEDBACK_SIZE, FEEDBACK_SIZE);
    lv_obj_align(ui.feedback, LV_ALIGN_TOP_MID, 0, (ART_SIZE - FEEDBACK_SIZE) / 2);
    lv_obj_set_style_bg_color(ui.feedback, lv_color_hex(0x080808), 0);
    lv_obj_set_style_bg_opa(ui.feedback, LV_OPA_80, 0);
    lv_obj_set_style_radius(ui.feedback, LV_RADIUS_CIRCLE, 0);
    lv_obj_remove_flag(ui.feedback, LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(ui.feedback, LV_OBJ_FLAG_HIDDEN);

    ui.feedback_glyph = lv_label_create(ui.feedback);
    lv_obj_set_style_text_font(ui.feedback_glyph, &lv_font_montserrat_24, 0);
    lv_obj_set_style_text_color(ui.feedback_glyph, lv_color_hex(0xFFFFFF), 0);
    lv_obj_center(ui.feedback_glyph);

    ui.gesture_layer = lv_obj_create(ui.screen);
    lv_obj_remove_style_all(ui.gesture_layer);
    lv_obj_set_size(ui.gesture_layer, SCREEN_WIDTH, SCREEN_HEIGHT);
    lv_obj_set_pos(ui.gesture_layer, 0, 0);
    lv_obj_add_flag(ui.gesture_layer, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_remove_flag(ui.gesture_layer, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_event_cb(ui.gesture_layer, gesture_event, LV_EVENT_PRESSED, NULL);
    lv_obj_add_event_cb(ui.gesture_layer, gesture_event, LV_EVENT_RELEASED, NULL);
    lv_obj_add_event_cb(ui.gesture_layer, gesture_event, LV_EVENT_LONG_PRESSED, NULL);
    lv_obj_move_foreground(ui.feedback);
}

static void show_playing(bool playing)
{
    lv_obj_t *playing_objects[] = {
        ui.band,
        ui.art_placeholder,
        ui.title,
        ui.artist,
        ui.progress,
        ui.elapsed,
        ui.total,
    };
    for (size_t index = 0; index < sizeof(playing_objects) / sizeof(playing_objects[0]); ++index) {
        if (playing) {
            lv_obj_remove_flag(playing_objects[index], LV_OBJ_FLAG_HIDDEN);
        } else {
            lv_obj_add_flag(playing_objects[index], LV_OBJ_FLAG_HIDDEN);
        }
    }
    if (!playing) {
        lv_obj_add_flag(ui.art_image, LV_OBJ_FLAG_HIDDEN);
        lv_obj_remove_flag(ui.idle, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(ui.idle, LV_OBJ_FLAG_HIDDEN);
    }
}

static uint32_t darken_color(uint32_t rgb)
{
    uint32_t red = ((rgb >> 16) & 0xff) * 42 / 100;
    uint32_t green = ((rgb >> 8) & 0xff) * 42 / 100;
    uint32_t blue = (rgb & 0xff) * 42 / 100;
    return (red << 16) | (green << 8) | blue;
}

static void apply_art(bop_album_art_t *art)
{
    if (!ui.state.available || strcmp(ui.state.track_id, art->track_id) != 0) {
        bop_album_art_free(art);
        return;
    }

    bop_album_art_t *previous = ui.active_art;
    lv_image_set_src(ui.art_image, &art->image);
    int32_t width = art->image.header.w;
    int32_t height = art->image.header.h;
    lv_obj_set_size(ui.art_image, width, height);
    lv_obj_set_pos(ui.art_image, (ART_SIZE - width) / 2, (ART_SIZE - height) / 2);
    lv_image_set_scale(ui.art_image, ART_SIZE * LV_SCALE_NONE / width);
    lv_obj_remove_flag(ui.art_image, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(ui.art_placeholder, LV_OBJ_FLAG_HIDDEN);

    uint32_t background = darken_color(art->dominant_rgb);
    lv_obj_set_style_bg_color(ui.band, lv_color_hex(background), 0);
    ui.active_art = art;
    bop_art_mark_active(art->track_id);
    if (previous != NULL) {
        lv_image_cache_drop(&previous->image);
        bop_album_art_free(previous);
    }
}

static void update_track(const playback_state_t *state)
{
    bool track_changed = strcmp(ui.state.track_id, state->track_id) != 0;
    ui.state = *state;
    ui.state_received_us = esp_timer_get_time();
    ui.rendered_change_counter = state->change_counter;

    if (!state->available) {
        if (ui.attribution_visible) {
            hide_attribution(NULL);
        }
        show_playing(false);
        lv_obj_set_style_bg_color(ui.screen, lv_color_hex(0x121212), 0);
        return;
    }

    show_playing(true);
    if (track_changed && ui.attribution_visible) {
        show_attribution();
    }
    lv_label_set_text(ui.title, state->title);
    lv_label_set_text(ui.artist, state->artists[0] != '\0' ? state->artists : "Spotify");
    if (track_changed) {
        if (ui.active_art != NULL
            && strcmp(ui.active_art->track_id, state->track_id) == 0) {
            lv_obj_remove_flag(ui.art_image, LV_OBJ_FLAG_HIDDEN);
            lv_obj_add_flag(ui.art_placeholder, LV_OBJ_FLAG_HIDDEN);
        } else {
            lv_obj_add_flag(ui.art_image, LV_OBJ_FLAG_HIDDEN);
            lv_obj_remove_flag(ui.art_placeholder, LV_OBJ_FLAG_HIDDEN);
        }
    }
}

static void format_time(uint32_t milliseconds, char *output, size_t capacity)
{
    uint32_t seconds = milliseconds / 1000;
    snprintf(output, capacity, "%u:%02u", (unsigned)(seconds / 60), (unsigned)(seconds % 60));
}

static void update_progress(void)
{
    if (!ui.state.available || ui.state.duration_ms == 0) {
        return;
    }
    uint64_t progress = ui.state.progress_ms;
    if (ui.state.is_playing) {
        int64_t elapsed_us = esp_timer_get_time() - ui.state_received_us;
        if (elapsed_us > 0) {
            progress += (uint64_t)elapsed_us / 1000;
        }
    }
    if (progress > ui.state.duration_ms) {
        progress = ui.state.duration_ms;
    }
    int32_t bar_value = (int32_t)(progress * 1000 / ui.state.duration_ms);
    lv_bar_set_value(ui.progress, bar_value, LV_ANIM_OFF);

    char elapsed[16];
    char total[16];
    format_time((uint32_t)progress, elapsed, sizeof(elapsed));
    format_time(ui.state.duration_ms, total, sizeof(total));
    lv_label_set_text(ui.elapsed, elapsed);
    lv_label_set_text(ui.total, total);
}

static void update_connectivity(void)
{
    if (bop_wifi_is_connected()) {
        lv_obj_add_flag(ui.offline, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_remove_flag(ui.offline, LV_OBJ_FLAG_HIDDEN);
    }
}

static void update_battery_care(void)
{
    if (!bop_power_on_battery()) {
        restore_normal_brightness();
        return;
    }
    if (ui.battery_dimmed
        || esp_timer_get_time() - ui.last_touch_us < BATTERY_DIM_TIMEOUT_US) {
        return;
    }
    esp_err_t error = bsp_display_brightness_set(DIM_BRIGHTNESS);
    if (error == ESP_OK) {
        ui.battery_dimmed = true;
        ESP_LOGI(TAG, "Dimmed display after 30 seconds of battery idle time");
    } else {
        ESP_LOGW(TAG, "Could not dim display: %s", esp_err_to_name(error));
    }
}

static void process_command_results(void)
{
    spotify_command_result_t result;
    while (bop_spotify_get_command_result(&result)) {
        if (result.command == SPOTIFY_COMMAND_TOGGLE
            && result.request_id != 0
            && result.accepted) {
            show_feedback(result.was_playing ? LV_SYMBOL_PAUSE : LV_SYMBOL_PLAY);
        }
    }
}

static void ui_timer(lv_timer_t *timer)
{
    (void)timer;
    update_connectivity();
    update_battery_care();
    bop_album_art_t *art = NULL;
    while (xQueueReceive(ui.art_queue, &art, 0) == pdTRUE) {
        apply_art(art);
    }
    process_command_results();

    playback_state_t snapshot = {0};
    if (bop_spotify_get_state(&snapshot)) {
        int64_t now = esp_timer_get_time();
        bool same_snapshot = snapshot.change_counter == ui.optimistic_change_counter;
        bool optimistic_expired = ui.optimistic_toggle
            && same_snapshot
            && now >= ui.optimistic_until_us;
        bool keep_optimistic = ui.optimistic_toggle
            && same_snapshot
            && !optimistic_expired;
        if (!keep_optimistic) {
            ui.optimistic_toggle = false;
            if (optimistic_expired
                || snapshot.change_counter != ui.rendered_change_counter) {
                update_track(&snapshot);
            }
        }
    }
    update_progress();
}

esp_err_t bop_ui_start(void)
{
    ui.art_queue = xQueueCreate(1, sizeof(bop_album_art_t *));
    if (ui.art_queue == NULL) {
        return ESP_ERR_NO_MEM;
    }
    esp_err_t error = bop_art_pipeline_start(ui.art_queue);
    if (error != ESP_OK) {
        vQueueDelete(ui.art_queue);
        ui.art_queue = NULL;
        return error;
    }

    ui.screen = lv_screen_active();
    lv_obj_remove_flag(ui.screen, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_style_pad_all(ui.screen, 0, 0);
    lv_obj_set_style_bg_color(ui.screen, lv_color_hex(0x121212), 0);
    lv_obj_set_style_bg_opa(ui.screen, LV_OPA_COVER, 0);

    create_playing_state();
    create_idle_state();
    create_offline_indicator();
    create_feedback_layer();
    create_attribution_screen();
    ui.last_touch_us = esp_timer_get_time();
    show_playing(false);
    lv_timer_create(ui_timer, UI_TIMER_MS, NULL);
    ESP_LOGI(TAG, "Now-playing UI is ready on core %d", UI_TASK_CORE);
    return ESP_OK;
}
