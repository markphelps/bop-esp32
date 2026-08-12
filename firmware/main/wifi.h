// SPDX-FileCopyrightText: 2026 Mark Phelps
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "credentials.h"
#include "esp_err.h"

typedef struct {
    bool connected;
    uint32_t connects;
    uint32_t disconnects;
    uint32_t reconnect_attempts;
} bop_wifi_status_t;

esp_err_t bop_wifi_connect(const bop_credentials_t *credentials);
bool bop_wifi_is_connected(void);
void bop_wifi_get_status(bop_wifi_status_t *status);
bool bop_wifi_wait_connected(void);
esp_err_t bop_time_sync(void);
