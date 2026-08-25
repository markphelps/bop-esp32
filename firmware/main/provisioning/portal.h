// SPDX-FileCopyrightText: 2026 Mark Phelps
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include "bop_esp_err.h"

#define BOP_PORTAL_AP_NAME_CAPACITY 15
#define BOP_PORTAL_AP_PASSWORD_CAPACITY 11
#define BOP_PORTAL_QR_PAYLOAD_CAPACITY 64

typedef struct {
    char ap_name[BOP_PORTAL_AP_NAME_CAPACITY];
    char ap_password[BOP_PORTAL_AP_PASSWORD_CAPACITY];
    char qr_payload[BOP_PORTAL_QR_PAYLOAD_CAPACITY];
} bop_portal_config_t;

esp_err_t bop_portal_prepare(bop_portal_config_t *configuration);
esp_err_t bop_portal_start(const bop_portal_config_t *configuration);
