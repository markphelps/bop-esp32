// SPDX-FileCopyrightText: 2026 Mark Phelps
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include <stdint.h>

#include "esp_err.h"
#include "lvgl.h"

esp_err_t bop_screenshot_init(void);
esp_err_t bop_screenshot_start(lv_display_t *display);
void bop_screenshot_mirror_area(const lv_area_t *area, const uint8_t *pixels);
