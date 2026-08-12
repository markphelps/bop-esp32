// SPDX-FileCopyrightText: 2026 Mark Phelps
// SPDX-License-Identifier: Apache-2.0

#include <stdbool.h>

#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_touch.h"
#include "bsp/esp32_s3_touch_amoled_1_8.h"
#include "bsp/display.h"
#include "bsp/touch.h"
#include "credentials.h"
#include "diagnostics.h"
#include "esp_log.h"
#include "esp_lvgl_port.h"
#include "esp_lvgl_port_touch.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "power.h"
#include "spotify/spotify.h"
#include "ui/ui.h"
#include "wifi.h"

#define LVGL_TASK_CORE 1
#define SPOTIFY_TASK_CORE 0
#define DISPLAY_BUFFER_ROWS 100
#define CO5300_QSPI_NOP 0x02000000

static const char *TAG = "spot";
static spot_credentials_t credentials;
static esp_lcd_panel_handle_t display_panel;
static esp_lcd_panel_io_handle_t display_panel_io;

static void create_placeholder(const char *text)
{
    lv_obj_t *screen = lv_screen_active();
    lv_obj_set_style_bg_color(screen, lv_color_hex(0x080808), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(screen, LV_OPA_COVER, LV_PART_MAIN);

    lv_obj_t *label = lv_label_create(screen);
    lv_label_set_text(label, text);
    lv_obj_set_style_text_align(label, LV_TEXT_ALIGN_CENTER, LV_PART_MAIN);
    lv_obj_set_style_text_color(label, lv_color_hex(0x1DB954), LV_PART_MAIN);
    lv_obj_set_style_text_font(label, &lv_font_montserrat_24, LV_PART_MAIN);
    lv_obj_center(label);
}

static void startup_task(void *argument)
{
    spot_credentials_t *configured_credentials = argument;
    esp_err_t error = spot_wifi_connect(configured_credentials);
    if (error == ESP_OK) {
        error = spot_time_sync();
    }
    if (error == ESP_OK) {
        error = spot_spotify_start(configured_credentials);
    }
    if (error != ESP_OK) {
        ESP_LOGE(TAG, "Startup failed: %s", esp_err_to_name(error));
    }
    vTaskDelete(NULL);
}

static void round_display_area(lv_event_t *event)
{
    lv_area_t *area = lv_event_get_param(event);
    area->x1 &= ~1;
    area->y1 &= ~1;
    area->x2 |= 1;
    area->y2 |= 1;
}

static void flush_display(lv_display_t *display, const lv_area_t *area, uint8_t *pixels)
{
    lv_draw_sw_rgb565_swap(pixels, lv_area_get_size(area));
    ESP_ERROR_CHECK(esp_lcd_panel_draw_bitmap(
        display_panel,
        area->x1,
        area->y1,
        area->x2 + 1,
        area->y2 + 1,
        pixels));

    /* Send an encoded QSPI NOP after the color transfer. The parameter
       transfer waits until DMA is complete before LVGL reuses its buffer. */
    ESP_ERROR_CHECK(
        esp_lcd_panel_io_tx_param(display_panel_io, CO5300_QSPI_NOP, NULL, 0));
    lv_display_flush_ready(display);
}

static esp_err_t recover_display_panel(void)
{
    esp_err_t error = esp_lcd_panel_disp_on_off(display_panel, false);
    if (error != ESP_OK) {
        return error;
    }
    vTaskDelay(pdMS_TO_TICKS(20));
    error = esp_lcd_panel_reset(display_panel);
    if (error != ESP_OK) {
        return error;
    }
    error = esp_lcd_panel_init(display_panel);
    if (error != ESP_OK) {
        return error;
    }
    return esp_lcd_panel_disp_on_off(display_panel, true);
}

static lv_display_t *start_display(void)
{
    lvgl_port_cfg_t lvgl_configuration = ESP_LVGL_PORT_INIT_CONFIG();
    lvgl_configuration.task_affinity = LVGL_TASK_CORE;
    if (lvgl_port_init(&lvgl_configuration) != ESP_OK) {
        return NULL;
    }

    bsp_display_config_t panel_configuration = {0};
    if (bsp_display_new(&panel_configuration, &display_panel, &display_panel_io) != ESP_OK) {
        return NULL;
    }
    esp_err_t recovery_error = recover_display_panel();
    if (recovery_error != ESP_OK) {
        ESP_LOGE(TAG, "Display panel recovery failed: %s", esp_err_to_name(recovery_error));
        return NULL;
    }

    const lvgl_port_display_cfg_t display_configuration = {
        .io_handle = display_panel_io,
        .panel_handle = display_panel,
        .buffer_size = BSP_LCD_H_RES * DISPLAY_BUFFER_ROWS,
        .double_buffer = false,
        .hres = BSP_LCD_H_RES,
        .vres = BSP_LCD_V_RES,
        .monochrome = false,
        .color_format = LV_COLOR_FORMAT_RGB565,
        .rotation = {
            .swap_xy = false,
            .mirror_x = false,
            .mirror_y = false,
        },
        .flags = {
            .sw_rotate = false,
            .buff_dma = true,
            .buff_spiram = false,
            .swap_bytes = false,
        },
    };
    if (!lvgl_port_lock(0)) {
        return NULL;
    }
    lv_display_t *display = lvgl_port_add_disp(&display_configuration);
    if (display == NULL) {
        lvgl_port_unlock();
        return NULL;
    }
    lv_display_set_flush_cb(display, flush_display);
    const esp_lcd_panel_io_callbacks_t no_callbacks = {0};
    esp_err_t callback_error = esp_lcd_panel_io_register_event_callbacks(
        display_panel_io, &no_callbacks, NULL);
    if (callback_error != ESP_OK) {
        lvgl_port_unlock();
        return NULL;
    }
    lv_display_add_event_cb(display, round_display_area, LV_EVENT_INVALIDATE_AREA, NULL);
    lvgl_port_unlock();

    esp_lcd_touch_handle_t touch = NULL;
    if (bsp_touch_new(NULL, &touch) != ESP_OK) {
        return NULL;
    }
    const lvgl_port_touch_cfg_t touch_configuration = {
        .disp = display,
        .handle = touch,
    };
    if (lvgl_port_add_touch(&touch_configuration) == NULL) {
        return NULL;
    }
    if (bsp_display_brightness_init() != ESP_OK) {
        return NULL;
    }
    return display;
}

void app_main(void)
{
    ESP_LOGI(TAG, "Starting the display");
    lv_display_t *display = start_display();
    if (display == NULL) {
        ESP_LOGE(TAG, "Display start failed");
        return;
    }
    ESP_ERROR_CHECK(bsp_display_brightness_set(85));
    esp_err_t power_error = spot_power_start();
    if (power_error != ESP_OK) {
        ESP_LOGW(TAG, "Battery monitor is unavailable: %s", esp_err_to_name(power_error));
    }

    esp_err_t credentials_error = spot_credentials_init();
    if (credentials_error == ESP_OK) {
        credentials_error = spot_credentials_load(&credentials);
    }
    const bool provisioned = credentials_error == ESP_OK;

    if (!bsp_display_lock(0)) {
        ESP_LOGE(TAG, "LVGL lock failed");
        return;
    }
    esp_err_t ui_error = ESP_OK;
    if (provisioned) {
        ui_error = spot_ui_start();
    } else {
        create_placeholder("run:\nmise run provision");
    }
    bsp_display_unlock();

    if (ui_error != ESP_OK) {
        ESP_LOGE(TAG, "UI start failed: %s", esp_err_to_name(ui_error));
        return;
    }
    if (!provisioned) {
        ESP_LOGW(TAG, "Provisioning values are missing: %s", esp_err_to_name(credentials_error));
        return;
    }
    esp_err_t diagnostics_error = spot_diagnostics_start();
    if (diagnostics_error != ESP_OK) {
        ESP_LOGW(TAG, "Soak diagnostics did not start: %s", esp_err_to_name(diagnostics_error));
    }
    if (xTaskCreatePinnedToCore(
            startup_task, "spot_startup", 16384, &credentials, 5, NULL, SPOTIFY_TASK_CORE)
        != pdPASS) {
        ESP_LOGE(TAG, "Startup task creation failed");
    }
}
