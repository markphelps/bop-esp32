// SPDX-FileCopyrightText: 2026 Mark Phelps
// SPDX-License-Identifier: Apache-2.0

#include "wifi.h"

#include <string.h>

#include "esp_check.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_netif_sntp.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"

#define WIFI_CONNECTED_BIT BIT0

static const char *TAG = "bop_wifi";
static EventGroupHandle_t connected_events;
static portMUX_TYPE status_lock = portMUX_INITIALIZER_UNLOCKED;
static bop_wifi_status_t status;

static void request_connection(bool reconnecting)
{
    if (reconnecting) {
        portENTER_CRITICAL(&status_lock);
        ++status.reconnect_attempts;
        portEXIT_CRITICAL(&status_lock);
    }
    esp_err_t error = esp_wifi_connect();
    if (error != ESP_OK) {
        ESP_LOGW(TAG, "WiFi connect request failed: %s", esp_err_to_name(error));
    }
}

static void wifi_event(void *argument, esp_event_base_t event_base, int32_t event_id, void *event_data)
{
    (void)argument;
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        request_connection(false);
        return;
    }
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        const wifi_event_sta_disconnected_t *event = event_data;
        xEventGroupClearBits(connected_events, WIFI_CONNECTED_BIT);
        portENTER_CRITICAL(&status_lock);
        ++status.disconnects;
        portEXIT_CRITICAL(&status_lock);
        ESP_LOGW(
            TAG,
            "WiFi disconnected (reason %d); reconnecting",
            event == NULL ? -1 : event->reason);
        request_connection(true);
        return;
    }
    if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        xEventGroupSetBits(connected_events, WIFI_CONNECTED_BIT);
        portENTER_CRITICAL(&status_lock);
        ++status.connects;
        portEXIT_CRITICAL(&status_lock);
        ESP_LOGI(TAG, "WiFi connected");
    }
}

esp_err_t bop_wifi_connect(const bop_credentials_t *credentials)
{
    if (credentials == NULL || credentials->wifi_ssid[0] == '\0') {
        return ESP_ERR_INVALID_ARG;
    }

    connected_events = xEventGroupCreate();
    if (connected_events == NULL) {
        return ESP_ERR_NO_MEM;
    }

    ESP_RETURN_ON_ERROR(esp_netif_init(), TAG, "Network interface initialization failed");
    esp_err_t error = esp_event_loop_create_default();
    if (error != ESP_OK && error != ESP_ERR_INVALID_STATE) {
        return error;
    }
    if (esp_netif_create_default_wifi_sta() == NULL) {
        return ESP_ERR_NO_MEM;
    }

    wifi_init_config_t initialization = WIFI_INIT_CONFIG_DEFAULT();
    ESP_RETURN_ON_ERROR(esp_wifi_init(&initialization), TAG, "WiFi initialization failed");
    ESP_RETURN_ON_ERROR(
        esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, wifi_event, NULL), TAG, "WiFi event registration failed");
    ESP_RETURN_ON_ERROR(
        esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, wifi_event, NULL), TAG, "IP event registration failed");

    wifi_config_t configuration = {0};
    memcpy(configuration.sta.ssid, credentials->wifi_ssid, strlen(credentials->wifi_ssid));
    memcpy(configuration.sta.password, credentials->wifi_password, strlen(credentials->wifi_password));
    configuration.sta.threshold.authmode = credentials->wifi_password[0] == '\0' ? WIFI_AUTH_OPEN : WIFI_AUTH_WPA2_PSK;
    configuration.sta.pmf_cfg.capable = true;
    configuration.sta.pmf_cfg.required = false;

    ESP_RETURN_ON_ERROR(esp_wifi_set_mode(WIFI_MODE_STA), TAG, "WiFi mode configuration failed");
    ESP_RETURN_ON_ERROR(esp_wifi_set_config(WIFI_IF_STA, &configuration), TAG, "WiFi station configuration failed");
    ESP_RETURN_ON_ERROR(esp_wifi_start(), TAG, "WiFi start failed");

    bop_wifi_wait_connected();
    return ESP_OK;
}

bool bop_wifi_is_connected(void)
{
    return connected_events != NULL
        && (xEventGroupGetBits(connected_events) & WIFI_CONNECTED_BIT) != 0;
}

void bop_wifi_get_status(bop_wifi_status_t *next_status)
{
    if (next_status == NULL) {
        return;
    }
    portENTER_CRITICAL(&status_lock);
    *next_status = status;
    portEXIT_CRITICAL(&status_lock);
    next_status->connected = bop_wifi_is_connected();
}

bool bop_wifi_wait_connected(void)
{
    if (connected_events == NULL) {
        return false;
    }
    EventBits_t bits = xEventGroupWaitBits(
        connected_events, WIFI_CONNECTED_BIT, pdFALSE, pdTRUE, portMAX_DELAY);
    return (bits & WIFI_CONNECTED_BIT) != 0;
}

esp_err_t bop_time_sync(void)
{
    ESP_LOGI(TAG, "Synchronizing time");
    esp_sntp_config_t configuration = ESP_NETIF_SNTP_DEFAULT_CONFIG("pool.ntp.org");
    ESP_RETURN_ON_ERROR(esp_netif_sntp_init(&configuration), TAG, "SNTP initialization failed");

    esp_err_t error = ESP_ERR_TIMEOUT;
    for (int attempt = 1; attempt <= 10 && error != ESP_OK; ++attempt) {
        error = esp_netif_sntp_sync_wait(pdMS_TO_TICKS(2000));
        if (error != ESP_OK) {
            ESP_LOGI(TAG, "Waiting for time (%d/10)", attempt);
        }
    }
    esp_netif_sntp_deinit();
    if (error == ESP_OK) {
        ESP_LOGI(TAG, "Time synchronized");
    }
    return error;
}
