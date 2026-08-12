// SPDX-FileCopyrightText: 2026 Mark Phelps
// SPDX-License-Identifier: Apache-2.0

#include "power.h"

#include <stdatomic.h>
#include <stdint.h>

#include "bsp/esp32_s3_touch_amoled_1_8.h"
#include "driver/i2c_master.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define AXP2101_ADDRESS 0x34
#define AXP2101_CHIP_ID_REGISTER 0x03
#define AXP2101_CHIP_ID 0x4A
#define AXP2101_STATUS1_REGISTER 0x00
#define AXP2101_BATTERY_CONNECTED_BIT (1U << 3)
#define AXP2101_VBUS_GOOD_BIT (1U << 5)
#define POWER_POLL_MS 5000

static const char *TAG = "spot_power";
static i2c_master_dev_handle_t power_device;
static atomic_bool on_battery;

static esp_err_t read_register(uint8_t address, uint8_t *value)
{
    return i2c_master_transmit_receive(power_device, &address, 1, value, 1, 1000);
}

static void update_power_source(void)
{
    uint8_t status = 0;
    esp_err_t error = read_register(AXP2101_STATUS1_REGISTER, &status);
    if (error != ESP_OK) {
        ESP_LOGW(TAG, "AXP2101 status read failed: %s", esp_err_to_name(error));
        return;
    }

    bool next_on_battery = (status & AXP2101_BATTERY_CONNECTED_BIT) != 0
        && (status & AXP2101_VBUS_GOOD_BIT) == 0;
    bool previous_on_battery = atomic_exchange(&on_battery, next_on_battery);
    if (previous_on_battery != next_on_battery) {
        ESP_LOGI(TAG, "Power source: %s", next_on_battery ? "battery" : "USB");
    }
}

static void power_task(void *argument)
{
    (void)argument;
    for (;;) {
        update_power_source();
        vTaskDelay(pdMS_TO_TICKS(POWER_POLL_MS));
    }
}

esp_err_t spot_power_start(void)
{
    if (power_device != NULL) {
        return ESP_ERR_INVALID_STATE;
    }

    i2c_master_bus_handle_t bus = bsp_i2c_get_handle();
    if (bus == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    const i2c_device_config_t configuration = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = AXP2101_ADDRESS,
        .scl_speed_hz = 100000,
    };
    esp_err_t error = i2c_master_bus_add_device(bus, &configuration, &power_device);
    if (error != ESP_OK) {
        return error;
    }

    uint8_t chip_id = 0;
    error = read_register(AXP2101_CHIP_ID_REGISTER, &chip_id);
    if (error != ESP_OK || chip_id != AXP2101_CHIP_ID) {
        ESP_LOGW(TAG, "AXP2101 probe failed (id=0x%02x)", chip_id);
        i2c_master_bus_rm_device(power_device);
        power_device = NULL;
        return error == ESP_OK ? ESP_ERR_NOT_FOUND : error;
    }

    update_power_source();
    if (xTaskCreate(power_task, "spot_power", 3072, NULL, 3, NULL) != pdPASS) {
        i2c_master_bus_rm_device(power_device);
        power_device = NULL;
        return ESP_ERR_NO_MEM;
    }
    ESP_LOGI(TAG, "AXP2101 power monitor started");
    return ESP_OK;
}

bool spot_power_on_battery(void)
{
    return atomic_load(&on_battery);
}
