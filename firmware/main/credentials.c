// SPDX-FileCopyrightText: 2026 Mark Phelps
// SPDX-License-Identifier: Apache-2.0

#include "credentials.h"

#include <string.h>

#include "mbedtls/platform_util.h"
#include "nvs.h"
#include "nvs_flash.h"

#define BOP_NVS_NAMESPACE "bop"

static esp_err_t load_string(nvs_handle_t handle, const char *key, char *output, size_t capacity)
{
    size_t required = 0;
    esp_err_t error = nvs_get_str(handle, key, NULL, &required);
    if (error != ESP_OK) {
        return error;
    }
    if (required == 0 || required > capacity) {
        return ESP_ERR_INVALID_SIZE;
    }
    return nvs_get_str(handle, key, output, &required);
}

esp_err_t bop_credentials_init(void)
{
    return nvs_flash_init();
}

esp_err_t bop_credentials_load(bop_credentials_t *credentials)
{
    if (credentials == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    mbedtls_platform_zeroize(credentials, sizeof(*credentials));

    nvs_handle_t handle;
    esp_err_t error = nvs_open(BOP_NVS_NAMESPACE, NVS_READONLY, &handle);
    if (error != ESP_OK) {
        return error;
    }

    error = load_string(handle, "wifi_ssid", credentials->wifi_ssid, sizeof(credentials->wifi_ssid));
    if (error == ESP_OK) {
        error = load_string(handle, "wifi_pass", credentials->wifi_password, sizeof(credentials->wifi_password));
    }
    if (error == ESP_OK) {
        error = load_string(handle, "client_id", credentials->client_id, sizeof(credentials->client_id));
    }
    if (error == ESP_OK) {
        error = load_string(handle, "refresh_tok", credentials->refresh_token, sizeof(credentials->refresh_token));
    }
    nvs_close(handle);
    if (error == ESP_OK
        && (credentials->wifi_ssid[0] == '\0'
            || credentials->client_id[0] == '\0'
            || credentials->refresh_token[0] == '\0')) {
        return ESP_ERR_INVALID_STATE;
    }
    return error;
}

esp_err_t bop_credentials_store_refresh_token(const char *refresh_token)
{
    if (refresh_token == NULL || refresh_token[0] == '\0') {
        return ESP_ERR_INVALID_ARG;
    }

    nvs_handle_t handle;
    esp_err_t error = nvs_open(BOP_NVS_NAMESPACE, NVS_READWRITE, &handle);
    if (error != ESP_OK) {
        return error;
    }
    error = nvs_set_str(handle, "refresh_tok", refresh_token);
    if (error == ESP_OK) {
        error = nvs_commit(handle);
    }
    nvs_close(handle);
    return error;
}
