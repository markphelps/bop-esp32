// SPDX-FileCopyrightText: 2026 Mark Phelps
// SPDX-License-Identifier: Apache-2.0

#include <stdarg.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "bsp/esp32_s3_touch_amoled_1_8.h"
#include "driver/usb_serial_jtag.h"
#include "driver/usb_serial_jtag_vfs.h"
#include "esp_crc.h"
#include "esp_err.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "screenshot.h"
#include "spotify/spotify.h"

#define BOP_SCREENSHOT_WIDTH 368U
#define BOP_SCREENSHOT_HEIGHT 448U
#define BOP_SCREENSHOT_PIXEL_BYTES 2U
#define BOP_SCREENSHOT_PAYLOAD_SIZE \
    (BOP_SCREENSHOT_WIDTH * BOP_SCREENSHOT_HEIGHT * BOP_SCREENSHOT_PIXEL_BYTES)
#define BOP_SCREENSHOT_HEADER_SIZE 24U
#define BOP_SCREENSHOT_VERSION 1U
#define BOP_SCREENSHOT_STATUS_SUCCESS 0U
#define BOP_SCREENSHOT_STATUS_NOT_READY 1U
#define BOP_SCREENSHOT_STATUS_NO_MEMORY 2U
#define BOP_SCREENSHOT_PIXEL_FORMAT_RGB565_BE 1U
#define BOP_SCREENSHOT_SERIAL_BUFFER_SIZE 4096U
#define BOP_SCREENSHOT_SERIAL_WRITE_SIZE 1024U
#define BOP_SCREENSHOT_TASK_STACK_SIZE 8192U
#define BOP_SCREENSHOT_TASK_CORE 0

static const char *TAG = "screenshot";
static uint8_t *mirror_buffer;
static uint8_t *staging_buffer;
static SemaphoreHandle_t mirror_mutex;
static SemaphoreHandle_t serial_output_mutex;
static vprintf_like_t original_vprintf;
static bool mirror_ready;

static int serial_log_vprintf(const char *format, va_list arguments)
{
    xSemaphoreTake(serial_output_mutex, portMAX_DELAY);
    int result = original_vprintf(format, arguments);
    xSemaphoreGive(serial_output_mutex);
    return result;
}

static void write_uint16_le(uint8_t *destination, uint16_t value)
{
    destination[0] = value & 0xffU;
    destination[1] = value >> 8;
}

static void write_uint32_le(uint8_t *destination, uint32_t value)
{
    destination[0] = value & 0xffU;
    destination[1] = (value >> 8) & 0xffU;
    destination[2] = (value >> 16) & 0xffU;
    destination[3] = value >> 24;
}

static void make_header(uint8_t header[BOP_SCREENSHOT_HEADER_SIZE], uint8_t status, uint32_t crc)
{
    memset(header, 0, BOP_SCREENSHOT_HEADER_SIZE);
    memcpy(header, "BOPS", 4);
    header[4] = BOP_SCREENSHOT_VERSION;
    header[5] = status;
    write_uint16_le(&header[6], BOP_SCREENSHOT_HEADER_SIZE);
    if (status == BOP_SCREENSHOT_STATUS_SUCCESS) {
        write_uint16_le(&header[8], BOP_SCREENSHOT_WIDTH);
        write_uint16_le(&header[10], BOP_SCREENSHOT_HEIGHT);
        write_uint16_le(&header[12], BOP_SCREENSHOT_PIXEL_FORMAT_RGB565_BE);
        write_uint32_le(&header[16], BOP_SCREENSHOT_PAYLOAD_SIZE);
        write_uint32_le(&header[20], crc);
    }
}

static bool write_serial_bytes(const uint8_t *source, size_t size)
{
    size_t offset = 0;
    while (offset < size) {
        size_t remaining = size - offset;
        size_t write_size = remaining < BOP_SCREENSHOT_SERIAL_WRITE_SIZE
            ? remaining
            : BOP_SCREENSHOT_SERIAL_WRITE_SIZE;
        int written = usb_serial_jtag_write_bytes(source + offset, write_size, portMAX_DELAY);
        if (written <= 0) {
            return false;
        }
        offset += (size_t)written;
    }
    return true;
}

static bool send_response(uint8_t status, uint32_t crc)
{
    uint8_t header[BOP_SCREENSHOT_HEADER_SIZE];
    make_header(header, status, crc);

    xSemaphoreTake(serial_output_mutex, portMAX_DELAY);
    bool sent = write_serial_bytes(header, sizeof(header));
    if (sent && status == BOP_SCREENSHOT_STATUS_SUCCESS) {
        sent = write_serial_bytes(staging_buffer, BOP_SCREENSHOT_PAYLOAD_SIZE);
    }
    if (sent) {
        sent = usb_serial_jtag_wait_tx_done(portMAX_DELAY) == ESP_OK;
    }
    xSemaphoreGive(serial_output_mutex);
    return sent;
}

static void send_screenshot(void)
{
    uint8_t status = BOP_SCREENSHOT_STATUS_NO_MEMORY;
    uint32_t crc = 0;
    if (mirror_buffer != NULL && staging_buffer != NULL) {
        xSemaphoreTakeRecursive(mirror_mutex, portMAX_DELAY);
        if (mirror_ready) {
            memcpy(staging_buffer, mirror_buffer, BOP_SCREENSHOT_PAYLOAD_SIZE);
            crc = esp_crc32_le(0, staging_buffer, BOP_SCREENSHOT_PAYLOAD_SIZE);
            status = BOP_SCREENSHOT_STATUS_SUCCESS;
        } else {
            status = BOP_SCREENSHOT_STATUS_NOT_READY;
        }
        xSemaphoreGiveRecursive(mirror_mutex);
    }
    if (!send_response(status, crc)) {
        ESP_LOGW(TAG, "Screenshot response did not finish");
    }
}

static void handle_spotify_command(uint8_t input)
{
    spotify_command_t command;
    if (input == 'n' || input == 'N') {
        command = SPOTIFY_COMMAND_NEXT;
    } else if (input == 'b' || input == 'B') {
        command = SPOTIFY_COMMAND_PREVIOUS;
    } else if (input == 't' || input == 'T') {
        command = SPOTIFY_COMMAND_TOGGLE;
    } else {
        return;
    }
    if (bop_spotify_commands_ready() && !bop_spotify_enqueue_command(command, 0)) {
        ESP_LOGW(TAG, "Command queue is full");
    }
}

static void render_event(lv_event_t *event)
{
    if (lv_event_get_code(event) == LV_EVENT_RENDER_START) {
        xSemaphoreTakeRecursive(mirror_mutex, portMAX_DELAY);
    } else if (lv_event_get_code(event) == LV_EVENT_RENDER_READY) {
        xSemaphoreGiveRecursive(mirror_mutex);
    }
}

static void refresh_mirror(lv_display_t *display)
{
    if (!bsp_display_lock(0)) {
        ESP_LOGW(TAG, "LVGL lock failed during screenshot refresh");
        return;
    }
    lv_obj_invalidate(lv_screen_active());
    lv_refr_now(display);
    xSemaphoreTakeRecursive(mirror_mutex, portMAX_DELAY);
    mirror_ready = true;
    xSemaphoreGiveRecursive(mirror_mutex);
    bsp_display_unlock();
}

static void screenshot_task(void *argument)
{
    lv_display_t *display = argument;
    refresh_mirror(display);
    for (;;) {
        uint8_t input;
        if (usb_serial_jtag_read_bytes(&input, 1, portMAX_DELAY) != 1) {
            continue;
        }
        if (input == 's') {
            refresh_mirror(display);
            send_screenshot();
        } else {
            handle_spotify_command(input);
        }
    }
}

esp_err_t bop_screenshot_init(void)
{
    mirror_mutex = xSemaphoreCreateRecursiveMutex();
    serial_output_mutex = xSemaphoreCreateMutex();
    if (mirror_mutex == NULL || serial_output_mutex == NULL) {
        return ESP_ERR_NO_MEM;
    }

    usb_serial_jtag_driver_config_t serial_configuration = {
        .rx_buffer_size = BOP_SCREENSHOT_SERIAL_BUFFER_SIZE,
        .tx_buffer_size = BOP_SCREENSHOT_SERIAL_BUFFER_SIZE,
    };
    esp_err_t error = usb_serial_jtag_driver_install(&serial_configuration);
    if (error != ESP_OK) {
        vSemaphoreDelete(mirror_mutex);
        vSemaphoreDelete(serial_output_mutex);
        mirror_mutex = NULL;
        serial_output_mutex = NULL;
        return error;
    }
    usb_serial_jtag_vfs_use_driver();
    original_vprintf = esp_log_set_vprintf(serial_log_vprintf);

    mirror_buffer = heap_caps_malloc(
        BOP_SCREENSHOT_PAYLOAD_SIZE, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    staging_buffer = heap_caps_malloc(
        BOP_SCREENSHOT_PAYLOAD_SIZE, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (mirror_buffer == NULL || staging_buffer == NULL) {
        heap_caps_free(mirror_buffer);
        heap_caps_free(staging_buffer);
        mirror_buffer = NULL;
        staging_buffer = NULL;
    }
    return ESP_OK;
}

esp_err_t bop_screenshot_start(lv_display_t *display)
{
    if (display == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (mirror_buffer != NULL && staging_buffer != NULL) {
        xSemaphoreTakeRecursive(mirror_mutex, portMAX_DELAY);
        mirror_ready = false;
        xSemaphoreGiveRecursive(mirror_mutex);
        lv_display_add_event_cb(display, render_event, LV_EVENT_RENDER_START, NULL);
        lv_display_add_event_cb(display, render_event, LV_EVENT_RENDER_READY, NULL);
    }
    if (xTaskCreatePinnedToCore(
            screenshot_task,
            "bop_screenshot",
            BOP_SCREENSHOT_TASK_STACK_SIZE,
            display,
            2,
            NULL,
            BOP_SCREENSHOT_TASK_CORE)
        != pdPASS) {
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

void bop_screenshot_mirror_area(const lv_area_t *area, const uint8_t *pixels)
{
    if (mirror_buffer == NULL || area->x1 < 0 || area->y1 < 0
        || area->x2 >= BOP_SCREENSHOT_WIDTH || area->y2 >= BOP_SCREENSHOT_HEIGHT) {
        return;
    }
    const size_t row_size = (size_t)(area->x2 - area->x1 + 1) * BOP_SCREENSHOT_PIXEL_BYTES;
    const size_t row_count = (size_t)(area->y2 - area->y1 + 1);
    xSemaphoreTakeRecursive(mirror_mutex, portMAX_DELAY);
    for (size_t row = 0; row < row_count; ++row) {
        size_t destination_offset =
            ((size_t)(area->y1 + row) * BOP_SCREENSHOT_WIDTH + area->x1)
            * BOP_SCREENSHOT_PIXEL_BYTES;
        memcpy(mirror_buffer + destination_offset, pixels + row * row_size, row_size);
    }
    xSemaphoreGiveRecursive(mirror_mutex);
}
