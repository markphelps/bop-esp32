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
    char track_id[BOP_TRACK_ID_CAPACITY];
} bop_album_art_t;

esp_err_t bop_art_pipeline_start(QueueHandle_t result_queue);
void bop_art_mark_active(const char *track_id);
void bop_album_art_free(bop_album_art_t *art);
