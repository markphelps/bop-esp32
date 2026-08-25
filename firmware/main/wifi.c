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
#include "freertos/semphr.h"
#include "freertos/task.h"

#define WIFI_CONNECTED_BIT BIT0
#define WIFI_DISCONNECTED_BIT BIT1
#define WIFI_EVENT_BARRIER_BIT BIT2
#define WIFI_ONLY_TIMEOUT pdMS_TO_TICKS(15000)
#define WIFI_DISCONNECT_TIMEOUT pdMS_TO_TICKS(2000)

enum {
    BOP_WIFI_EVENT_BARRIER,
};

ESP_EVENT_DEFINE_BASE(BOP_WIFI_EVENT);

static const char *TAG = "bop_wifi";
static EventGroupHandle_t connected_events;
static SemaphoreHandle_t connection_mutex;
static portMUX_TYPE status_lock = portMUX_INITIALIZER_UNLOCKED;
static bop_wifi_status_t status;
static bool initialization_attempted;
static esp_err_t initialization_result = ESP_ERR_INVALID_STATE;
static bool wifi_started;
static bool reconnect_enabled;
static uint32_t generation_counter;
static uint32_t active_generation;
static uint32_t associated_generation;
static uint32_t connected_generation;

static uint32_t next_generation_locked(void)
{
    ++generation_counter;
    if (generation_counter == 0) {
        ++generation_counter;
    }
    return generation_counter;
}

static void invalidate_attempt_locked(void)
{
    next_generation_locked();
    active_generation = 0;
    associated_generation = 0;
    connected_generation = 0;
    reconnect_enabled = false;
}

static esp_err_t wait_for_event_barrier_locked(void)
{
    xEventGroupClearBits(connected_events, WIFI_EVENT_BARRIER_BIT);
    esp_err_t error = esp_event_post(
        BOP_WIFI_EVENT,
        BOP_WIFI_EVENT_BARRIER,
        NULL,
        0,
        WIFI_DISCONNECT_TIMEOUT);
    if (error != ESP_OK) {
        return error;
    }

    EventBits_t bits = xEventGroupWaitBits(
        connected_events,
        WIFI_EVENT_BARRIER_BIT,
        pdTRUE,
        pdTRUE,
        WIFI_DISCONNECT_TIMEOUT);
    return (bits & WIFI_EVENT_BARRIER_BIT) != 0 ? ESP_OK : ESP_ERR_TIMEOUT;
}

static esp_err_t disconnect_station_locked(void)
{
    if (!wifi_started) {
        return ESP_OK;
    }

    xEventGroupClearBits(connected_events, WIFI_DISCONNECTED_BIT);
    esp_err_t error = esp_wifi_disconnect();
    if (error != ESP_OK && error != ESP_ERR_WIFI_NOT_CONNECT) {
        return error;
    }
    if (error == ESP_OK) {
        EventBits_t bits = xEventGroupWaitBits(
            connected_events,
            WIFI_DISCONNECTED_BIT,
            pdTRUE,
            pdTRUE,
            WIFI_DISCONNECT_TIMEOUT);
        if ((bits & WIFI_DISCONNECTED_BIT) == 0) {
            return ESP_ERR_TIMEOUT;
        }
    }

    /* Drain older WiFi and IP events while there is no active attempt. */
    return wait_for_event_barrier_locked();
}

static bool request_connection(uint32_t generation, bool reconnecting)
{
    /* The event loop also delivers cleanup barriers, so it must not wait for
       a mutex held by the cleanup path. */
    if (xSemaphoreTake(connection_mutex, 0) != pdTRUE) {
        return false;
    }

    portENTER_CRITICAL(&status_lock);
    const bool current = reconnect_enabled && active_generation == generation;
    if (current && reconnecting) {
        ++status.reconnect_attempts;
    }
    portEXIT_CRITICAL(&status_lock);

    esp_err_t error = ESP_ERR_INVALID_STATE;
    if (current) {
        error = esp_wifi_connect();
    }
    xSemaphoreGive(connection_mutex);

    if (current && error != ESP_OK) {
        ESP_LOGW(TAG, "WiFi connect request failed: %s", esp_err_to_name(error));
    }
    return current && error == ESP_OK;
}

static void wifi_event(void *argument, esp_event_base_t event_base, int32_t event_id, void *event_data)
{
    (void)argument;
    if (event_base == BOP_WIFI_EVENT && event_id == BOP_WIFI_EVENT_BARRIER) {
        xEventGroupSetBits(connected_events, WIFI_EVENT_BARRIER_BIT);
        return;
    }
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_CONNECTED) {
        portENTER_CRITICAL(&status_lock);
        if (reconnect_enabled && active_generation != 0) {
            associated_generation = active_generation;
        }
        portEXIT_CRITICAL(&status_lock);
        return;
    }
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        const wifi_event_sta_disconnected_t *event = event_data;
        portENTER_CRITICAL(&status_lock);
        ++status.disconnects;
        connected_generation = 0;
        associated_generation = 0;
        const uint32_t generation = active_generation;
        const bool reconnecting = reconnect_enabled && generation != 0;
        portEXIT_CRITICAL(&status_lock);
        xEventGroupClearBits(connected_events, WIFI_CONNECTED_BIT);
        xEventGroupSetBits(connected_events, WIFI_DISCONNECTED_BIT);
        if (reconnecting) {
            ESP_LOGW(
                TAG,
                "WiFi disconnected (reason %d); reconnecting",
                event == NULL ? -1 : event->reason);
            request_connection(generation, true);
        } else {
            ESP_LOGI(TAG, "WiFi station disconnected");
        }
        return;
    }
    if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        portENTER_CRITICAL(&status_lock);
        const bool current = active_generation != 0
            && associated_generation == active_generation;
        if (current) {
            connected_generation = active_generation;
            ++status.connects;
        }
        portEXIT_CRITICAL(&status_lock);
        if (current) {
            xEventGroupSetBits(connected_events, WIFI_CONNECTED_BIT);
            ESP_LOGI(TAG, "WiFi connected");
        } else {
            ESP_LOGW(TAG, "Ignored an IP event from an inactive WiFi attempt");
        }
    }
}

static esp_err_t initialize_services(void)
{
    connected_events = xEventGroupCreate();
    if (connected_events == NULL) {
        return ESP_ERR_NO_MEM;
    }
    connection_mutex = xSemaphoreCreateMutex();
    if (connection_mutex == NULL) {
        vEventGroupDelete(connected_events);
        connected_events = NULL;
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
        esp_wifi_set_storage(WIFI_STORAGE_RAM), TAG, "WiFi RAM storage configuration failed");
    ESP_RETURN_ON_ERROR(
        esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, wifi_event, NULL), TAG, "WiFi event registration failed");
    ESP_RETURN_ON_ERROR(
        esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, wifi_event, NULL), TAG, "IP event registration failed");
    ESP_RETURN_ON_ERROR(
        esp_event_handler_register(BOP_WIFI_EVENT, BOP_WIFI_EVENT_BARRIER, wifi_event, NULL), TAG, "WiFi barrier registration failed");
    return ESP_OK;
}

esp_err_t bop_wifi_init(void)
{
    if (!initialization_attempted) {
        initialization_attempted = true;
        initialization_result = initialize_services();
    }
    return initialization_result;
}

static bool wait_for_attempt(uint32_t generation, TickType_t timeout)
{
    const TickType_t start = xTaskGetTickCount();
    TickType_t remaining = timeout;
    while (true) {
        EventBits_t bits = xEventGroupWaitBits(
            connected_events, WIFI_CONNECTED_BIT, pdFALSE, pdTRUE, remaining);
        if ((bits & WIFI_CONNECTED_BIT) == 0) {
            return false;
        }

        portENTER_CRITICAL(&status_lock);
        const bool connected = connected_generation == generation;
        portEXIT_CRITICAL(&status_lock);
        if (connected) {
            return true;
        }
        if (timeout == portMAX_DELAY) {
            continue;
        }

        const TickType_t elapsed = xTaskGetTickCount() - start;
        if (elapsed >= timeout) {
            return false;
        }
        remaining = timeout - elapsed;
    }
}

static esp_err_t start_station_attempt(
    const bop_credentials_t *credentials, bool bounded, uint32_t *generation)
{
    if (initialization_result != ESP_OK) {
        return ESP_ERR_INVALID_STATE;
    }
    if (credentials == NULL
        || credentials->wifi_ssid[0] == '\0'
        || strnlen(credentials->wifi_ssid, BOP_WIFI_SSID_CAPACITY) == BOP_WIFI_SSID_CAPACITY
        || strnlen(credentials->wifi_password, BOP_WIFI_PASSWORD_CAPACITY) == BOP_WIFI_PASSWORD_CAPACITY) {
        return ESP_ERR_INVALID_ARG;
    }
    if (xSemaphoreTake(connection_mutex, portMAX_DELAY) != pdTRUE) {
        return ESP_ERR_TIMEOUT;
    }

    wifi_mode_t mode = WIFI_MODE_STA;
    esp_err_t error = ESP_OK;
    if (bounded) {
        error = esp_wifi_get_mode(&mode);
        if (error == ESP_OK) {
            mode = mode == WIFI_MODE_AP || mode == WIFI_MODE_APSTA
                ? WIFI_MODE_APSTA
                : WIFI_MODE_STA;
        }
    }

    portENTER_CRITICAL(&status_lock);
    const bool had_attempt = active_generation != 0;
    invalidate_attempt_locked();
    portEXIT_CRITICAL(&status_lock);
    xEventGroupClearBits(connected_events, WIFI_CONNECTED_BIT);
    if (error == ESP_OK && had_attempt) {
        error = disconnect_station_locked();
    }

    wifi_config_t configuration = {0};
    if (error == ESP_OK) {
        memcpy(configuration.sta.ssid, credentials->wifi_ssid, strlen(credentials->wifi_ssid));
        memcpy(configuration.sta.password, credentials->wifi_password, strlen(credentials->wifi_password));
        configuration.sta.threshold.authmode = credentials->wifi_password[0] == '\0'
            ? WIFI_AUTH_OPEN
            : WIFI_AUTH_WPA2_PSK;
        configuration.sta.pmf_cfg.capable = true;
        configuration.sta.pmf_cfg.required = false;
        error = esp_wifi_set_mode(mode);
    }
    if (error == ESP_OK) {
        error = esp_wifi_set_config(WIFI_IF_STA, &configuration);
    }

    uint32_t next_generation = 0;
    if (error == ESP_OK) {
        portENTER_CRITICAL(&status_lock);
        next_generation = next_generation_locked();
        active_generation = next_generation;
        reconnect_enabled = true;
        portEXIT_CRITICAL(&status_lock);
        if (!wifi_started) {
            error = esp_wifi_start();
            if (error == ESP_OK) {
                wifi_started = true;
            }
        }
    }
    if (error == ESP_OK) {
        error = esp_wifi_connect();
    }
    if (error != ESP_OK) {
        portENTER_CRITICAL(&status_lock);
        invalidate_attempt_locked();
        portEXIT_CRITICAL(&status_lock);
    }
    xSemaphoreGive(connection_mutex);

    if (error != ESP_OK) {
        return error;
    }
    *generation = next_generation;
    return ESP_OK;
}

esp_err_t bop_wifi_connect(const bop_credentials_t *credentials)
{
    uint32_t generation;
    esp_err_t error = start_station_attempt(credentials, false, &generation);
    if (error != ESP_OK) {
        return error;
    }
    return wait_for_attempt(generation, portMAX_DELAY) ? ESP_OK : ESP_ERR_INVALID_STATE;
}

esp_err_t bop_wifi_connect_bounded(const bop_credentials_t *credentials)
{
    uint32_t generation;
    esp_err_t error = start_station_attempt(credentials, true, &generation);
    if (error != ESP_OK) {
        return error;
    }
    if (wait_for_attempt(generation, WIFI_ONLY_TIMEOUT)) {
        return ESP_OK;
    }

    error = bop_wifi_prepare_portal();
    if (error != ESP_OK) {
        ESP_LOGW(TAG, "WiFi timeout cleanup failed: %s", esp_err_to_name(error));
        return ESP_ERR_INVALID_STATE;
    }
    return ESP_ERR_TIMEOUT;
}

esp_err_t bop_wifi_prepare_portal(void)
{
    if (initialization_result != ESP_OK) {
        return ESP_ERR_INVALID_STATE;
    }
    if (xSemaphoreTake(connection_mutex, portMAX_DELAY) != pdTRUE) {
        return ESP_ERR_TIMEOUT;
    }

    portENTER_CRITICAL(&status_lock);
    const bool had_attempt = active_generation != 0;
    invalidate_attempt_locked();
    portEXIT_CRITICAL(&status_lock);
    xEventGroupClearBits(connected_events, WIFI_CONNECTED_BIT);

    esp_err_t error = ESP_OK;
    if (had_attempt) {
        error = disconnect_station_locked();
    }
    xSemaphoreGive(connection_mutex);
    return error;
}

esp_err_t bop_wifi_start_portal_ap(const char *ssid, const char *password)
{
    if (initialization_result != ESP_OK) {
        return ESP_ERR_INVALID_STATE;
    }
    if (ssid == NULL || password == NULL || ssid[0] == '\0') {
        return ESP_ERR_INVALID_ARG;
    }
    const size_t ssid_length = strnlen(ssid, sizeof(((wifi_config_t *)0)->ap.ssid));
    const size_t password_length = strnlen(password, sizeof(((wifi_config_t *)0)->ap.password));
    if (ssid_length == sizeof(((wifi_config_t *)0)->ap.ssid)
        || password_length < 8
        || password_length == sizeof(((wifi_config_t *)0)->ap.password)) {
        return ESP_ERR_INVALID_SIZE;
    }
    if (xSemaphoreTake(connection_mutex, portMAX_DELAY) != pdTRUE) {
        return ESP_ERR_TIMEOUT;
    }

    portENTER_CRITICAL(&status_lock);
    const bool had_attempt = active_generation != 0;
    invalidate_attempt_locked();
    portEXIT_CRITICAL(&status_lock);
    xEventGroupClearBits(connected_events, WIFI_CONNECTED_BIT);

    esp_err_t error = ESP_OK;
    if (had_attempt) {
        error = disconnect_station_locked();
    }
    if (error == ESP_OK && wifi_started) {
        error = esp_wifi_stop();
        if (error == ESP_OK) {
            wifi_started = false;
        }
    }

    wifi_config_t configuration = {0};
    if (error == ESP_OK) {
        memcpy(configuration.ap.ssid, ssid, ssid_length);
        configuration.ap.ssid_len = ssid_length;
        memcpy(configuration.ap.password, password, password_length);
        configuration.ap.channel = 1;
        configuration.ap.authmode = WIFI_AUTH_WPA2_PSK;
        configuration.ap.max_connection = 4;
        configuration.ap.pmf_cfg.capable = true;
        configuration.ap.pmf_cfg.required = false;
        error = esp_wifi_set_mode(WIFI_MODE_APSTA);
    }
    if (error == ESP_OK) {
        error = esp_wifi_set_config(WIFI_IF_AP, &configuration);
    }
    if (error == ESP_OK) {
        error = esp_wifi_start();
        if (error == ESP_OK) {
            wifi_started = true;
        }
    }
    xSemaphoreGive(connection_mutex);
    return error;
}

esp_err_t bop_wifi_stop_portal_ap(void)
{
    if (initialization_result != ESP_OK) {
        return ESP_ERR_INVALID_STATE;
    }
    if (xSemaphoreTake(connection_mutex, portMAX_DELAY) != pdTRUE) {
        return ESP_ERR_TIMEOUT;
    }

    portENTER_CRITICAL(&status_lock);
    const bool had_attempt = active_generation != 0;
    invalidate_attempt_locked();
    portEXIT_CRITICAL(&status_lock);
    xEventGroupClearBits(connected_events, WIFI_CONNECTED_BIT);

    esp_err_t error = ESP_OK;
    if (had_attempt) {
        error = disconnect_station_locked();
    }
    if (wifi_started) {
        esp_err_t stop_error = esp_wifi_stop();
        if (stop_error == ESP_OK) {
            wifi_started = false;
        }
        if (error == ESP_OK) {
            error = stop_error;
        }
    }
    xSemaphoreGive(connection_mutex);
    return error;
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
