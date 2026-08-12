// SPDX-FileCopyrightText: 2026 Mark Phelps
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include "esp_err.h"

#define BOP_WIFI_SSID_CAPACITY 33
#define BOP_WIFI_PASSWORD_CAPACITY 65
#define BOP_CLIENT_ID_CAPACITY 65
#define BOP_REFRESH_TOKEN_CAPACITY 1024

typedef struct {
    char wifi_ssid[BOP_WIFI_SSID_CAPACITY];
    char wifi_password[BOP_WIFI_PASSWORD_CAPACITY];
    char client_id[BOP_CLIENT_ID_CAPACITY];
    char refresh_token[BOP_REFRESH_TOKEN_CAPACITY];
} bop_credentials_t;

esp_err_t bop_credentials_init(void);
esp_err_t bop_credentials_load(bop_credentials_t *credentials);
esp_err_t bop_credentials_store_refresh_token(const char *refresh_token);
