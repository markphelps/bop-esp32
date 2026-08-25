// SPDX-FileCopyrightText: 2026 Mark Phelps
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "bop_esp_err.h"
#include "credentials.h"

typedef struct {
    bool connected;
    uint32_t connects;
    uint32_t disconnects;
    uint32_t reconnect_attempts;
} bop_wifi_status_t;

esp_err_t bop_wifi_init(void);
esp_err_t bop_wifi_connect(const bop_credentials_t *credentials);
esp_err_t bop_wifi_connect_bounded(const bop_credentials_t *credentials);
esp_err_t bop_wifi_prepare_portal(void);
esp_err_t bop_wifi_start_portal_ap(const char *ssid, const char *password);
esp_err_t bop_wifi_stop_portal_ap(void);
bool bop_wifi_is_connected(void);
void bop_wifi_get_status(bop_wifi_status_t *status);
bool bop_wifi_wait_connected(void);
esp_err_t bop_time_sync(void);
