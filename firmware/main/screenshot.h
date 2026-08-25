// SPDX-FileCopyrightText: 2026 Mark Phelps
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include <stdint.h>

#include "esp_err.h"
#include "lvgl.h"

#include "credentials.h"

#define BOP_USB_START_BYTE 0x7fU
#define BOP_USB_PROTOCOL_VERSION 1U
#define BOP_USB_HEADER_SIZE 16U
#define BOP_USB_MAX_PAYLOAD_SIZE (BOP_CLIENT_ID_CAPACITY + BOP_REFRESH_TOKEN_CAPACITY + 4U)
#define BOP_USB_QUERY_COMMAND 1U
#define BOP_USB_STORE_SPOTIFY_COMMAND 2U
#define BOP_USB_STATUS_OK 0U
#define BOP_USB_STATUS_NO_CREDENTIALS 1U
#define BOP_USB_STATUS_MALFORMED 2U
#define BOP_USB_STATUS_UNSUPPORTED_VERSION 3U
#define BOP_USB_STATUS_INVALID_LENGTH 4U
#define BOP_USB_STATUS_INTEGRITY 5U
#define BOP_USB_STATUS_STORAGE 6U
#define BOP_USB_STATUS_UNSUPPORTED_COMMAND 7U

esp_err_t bop_screenshot_init(void);
esp_err_t bop_screenshot_start(lv_display_t *display);
void bop_screenshot_mirror_area(const lv_area_t *area, const uint8_t *pixels);
