// SPDX-FileCopyrightText: 2026 Mark Phelps
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include "esp_err.h"

#define SPOT_WIFI_SSID_CAPACITY 33
#define SPOT_WIFI_PASSWORD_CAPACITY 65
#define SPOT_CLIENT_ID_CAPACITY 65
#define SPOT_REFRESH_TOKEN_CAPACITY 1024

typedef struct {
    char wifi_ssid[SPOT_WIFI_SSID_CAPACITY];
    char wifi_password[SPOT_WIFI_PASSWORD_CAPACITY];
    char client_id[SPOT_CLIENT_ID_CAPACITY];
    char refresh_token[SPOT_REFRESH_TOKEN_CAPACITY];
} spot_credentials_t;

esp_err_t spot_credentials_init(void);
esp_err_t spot_credentials_load(spot_credentials_t *credentials);
esp_err_t spot_credentials_store_refresh_token(const char *refresh_token);
