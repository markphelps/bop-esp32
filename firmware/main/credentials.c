// SPDX-FileCopyrightText: 2026 Mark Phelps
// SPDX-License-Identifier: Apache-2.0

#include "credentials.h"

#include <stdbool.h>
#include <string.h>

#include "nvs.h"
#include "nvs_flash.h"

#define BOP_NVS_NAMESPACE "bop"

extern void mbedtls_platform_zeroize(void *buffer, size_t length);

static esp_err_t load_optional_string(
    nvs_handle_t handle, const char *key, char *output, size_t capacity)
{
    output[0] = '\0';
    size_t required = 0;
    esp_err_t error = nvs_get_str(handle, key, NULL, &required);
    if (error == ESP_ERR_NVS_NOT_FOUND) {
        return ESP_OK;
    }
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
    if (error == ESP_ERR_NVS_NOT_FOUND) {
        return ESP_OK;
    }
    if (error != ESP_OK) {
        return error;
    }

    error = load_optional_string(
        handle, "wifi_ssid", credentials->wifi_ssid, sizeof(credentials->wifi_ssid));
    if (error == ESP_OK) {
        error = load_optional_string(
            handle, "wifi_pass", credentials->wifi_password, sizeof(credentials->wifi_password));
    }
    if (error == ESP_OK) {
        error = load_optional_string(
            handle, "client_id", credentials->client_id, sizeof(credentials->client_id));
    }
    if (error == ESP_OK) {
        error = load_optional_string(
            handle, "refresh_tok", credentials->refresh_token, sizeof(credentials->refresh_token));
    }
    nvs_close(handle);
    return error;
}

esp_err_t bop_credentials_state(
    const bop_credentials_t *credentials, bop_provision_state_t *state)
{
    if (credentials == NULL || state == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    const bool has_ssid = credentials->wifi_ssid[0] != '\0';
    const bool has_password = credentials->wifi_password[0] != '\0';
    const bool has_client_id = credentials->client_id[0] != '\0';
    const bool has_refresh_token = credentials->refresh_token[0] != '\0';

    bop_provision_state_t next_state;
    if (!has_ssid && !has_password && !has_client_id && !has_refresh_token) {
        next_state = BOP_PROVISION_NONE;
    } else if (has_ssid && !has_client_id && !has_refresh_token) {
        next_state = BOP_PROVISION_WIFI_ONLY;
    } else if (has_ssid && has_client_id && has_refresh_token) {
        next_state = BOP_PROVISION_COMPLETE;
    } else {
        return ESP_ERR_INVALID_STATE;
    }

    *state = next_state;
    return ESP_OK;
}

esp_err_t bop_credentials_store_wifi(const char *ssid, const char *password)
{
    if (ssid == NULL || password == NULL || ssid[0] == '\0') {
        return ESP_ERR_INVALID_ARG;
    }
    if (strnlen(ssid, BOP_WIFI_SSID_CAPACITY) == BOP_WIFI_SSID_CAPACITY
        || strnlen(password, BOP_WIFI_PASSWORD_CAPACITY) == BOP_WIFI_PASSWORD_CAPACITY) {
        return ESP_ERR_INVALID_SIZE;
    }

    nvs_handle_t handle;
    esp_err_t error = nvs_open(BOP_NVS_NAMESPACE, NVS_READWRITE, &handle);
    if (error != ESP_OK) {
        return error;
    }
    error = nvs_set_str(handle, "wifi_ssid", ssid);
    if (error == ESP_OK) {
        error = nvs_set_str(handle, "wifi_pass", password);
    }
    if (error == ESP_OK) {
        error = nvs_commit(handle);
    }
    nvs_close(handle);
    return error;
}

esp_err_t bop_credentials_store_spotify(
    const char *client_id, const char *refresh_token)
{
    if (client_id == NULL || refresh_token == NULL || client_id[0] == '\0'
        || refresh_token[0] == '\0') {
        return ESP_ERR_INVALID_ARG;
    }
    if (strnlen(client_id, BOP_CLIENT_ID_CAPACITY) == BOP_CLIENT_ID_CAPACITY
        || strnlen(refresh_token, BOP_REFRESH_TOKEN_CAPACITY) == BOP_REFRESH_TOKEN_CAPACITY) {
        return ESP_ERR_INVALID_SIZE;
    }

    nvs_handle_t handle;
    esp_err_t error = nvs_open(BOP_NVS_NAMESPACE, NVS_READWRITE, &handle);
    if (error != ESP_OK) {
        return error;
    }
    error = nvs_set_str(handle, "client_id", client_id);
    if (error == ESP_OK) {
        error = nvs_set_str(handle, "refresh_tok", refresh_token);
    }
    if (error == ESP_OK) {
        error = nvs_commit(handle);
    }
    nvs_close(handle);
    return error;
}

esp_err_t bop_credentials_load_state(bop_provision_state_t *state)
{
    if (state == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    bop_credentials_t credentials;
    esp_err_t error = bop_credentials_load(&credentials);
    if (error == ESP_OK) {
        error = bop_credentials_state(&credentials, state);
    }
    mbedtls_platform_zeroize(&credentials, sizeof(credentials));
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
