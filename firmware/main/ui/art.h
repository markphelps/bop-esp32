// SPDX-FileCopyrightText: 2026 Mark Phelps
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include <stdint.h>

#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "lvgl.h"
#include "../spotify/spotify.h"

typedef struct {
    lv_image_dsc_t image;
    uint8_t *pixels;
    uint32_t dominant_rgb;
    char track_id[SPOT_TRACK_ID_CAPACITY];
} spot_album_art_t;

esp_err_t spot_art_pipeline_start(QueueHandle_t result_queue);
void spot_art_mark_active(const char *track_id);
void spot_album_art_free(spot_album_art_t *art);
