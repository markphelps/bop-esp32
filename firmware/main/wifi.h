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
} spot_wifi_status_t;

esp_err_t spot_wifi_connect(const spot_credentials_t *credentials);
bool spot_wifi_is_connected(void);
void spot_wifi_get_status(spot_wifi_status_t *status);
bool spot_wifi_wait_connected(void);
esp_err_t spot_time_sync(void);
