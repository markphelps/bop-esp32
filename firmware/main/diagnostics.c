// SPDX-FileCopyrightText: 2026 Mark Phelps
// SPDX-License-Identifier: Apache-2.0

#include "diagnostics.h"

#include <inttypes.h>

#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "wifi.h"

#define SOAK_LOG_INTERVAL_MS 60000

static const char *TAG = "spot_soak";

static void diagnostics_task(void *argument)
{
    (void)argument;
    for (;;) {
        spot_wifi_status_t wifi_status = {0};
        spot_wifi_get_status(&wifi_status);
        ESP_LOGI(
            TAG,
            "soak heap_free=%u heap_min_free=%u psram_free=%u psram_min_free=%u "
            "wifi=%s connects=%" PRIu32 " disconnects=%" PRIu32 " reconnects=%" PRIu32,
            (unsigned)esp_get_free_heap_size(),
            (unsigned)esp_get_minimum_free_heap_size(),
            (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM),
            (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_SPIRAM),
            wifi_status.connected ? "connected" : "offline",
            wifi_status.connects,
            wifi_status.disconnects,
            wifi_status.reconnect_attempts);
        vTaskDelay(pdMS_TO_TICKS(SOAK_LOG_INTERVAL_MS));
    }
}

esp_err_t spot_diagnostics_start(void)
{
    if (xTaskCreate(diagnostics_task, "spot_soak", 3072, NULL, 2, NULL) != pdPASS) {
        return ESP_ERR_NO_MEM;
    }
    ESP_LOGI(TAG, "Soak diagnostics log every %d seconds", SOAK_LOG_INTERVAL_MS / 1000);
    return ESP_OK;
}
