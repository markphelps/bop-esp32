// SPDX-FileCopyrightText: 2026 Mark Phelps
// SPDX-License-Identifier: Apache-2.0

#include <stdbool.h>
#include <string.h>

#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_touch.h"
#include "bsp/esp32_s3_touch_amoled_1_8.h"
#include "bsp/display.h"
#include "bsp/touch.h"
#include "credentials.h"
#include "esp_log.h"
#include "esp_lvgl_port.h"
#include "esp_lvgl_port_touch.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "power.h"
#include "provisioning/portal.h"
#include "screenshot.h"
#include "spotify/spotify.h"
#include "ui/ui.h"
#include "wifi.h"

#define LVGL_TASK_CORE 1
#define SPOTIFY_TASK_CORE 0
#define DISPLAY_BUFFER_ROWS 100
// The BSP defines a zero display-lock timeout as an indefinite wait.
#define DISPLAY_LOCK_FOREVER_MS 0
#define CO5300_QSPI_NOP 0x02000000

static const char *TAG = "bop";
static bop_credentials_t credentials;
static bop_provision_state_t provision_state = BOP_PROVISION_NONE;
static bop_portal_config_t portal_configuration;
static esp_lcd_panel_handle_t display_panel;
static esp_lcd_panel_io_handle_t display_panel_io;

static lv_obj_t *prepare_setup_screen(void)
{
    lv_obj_t *screen = lv_screen_active();
    lv_obj_clean(screen);
    lv_obj_set_style_bg_color(screen, lv_color_hex(0x080808), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(screen, LV_OPA_COVER, LV_PART_MAIN);
    return screen;
}

static void create_placeholder(const char *text)
{
    lv_obj_t *screen = prepare_setup_screen();
    lv_obj_t *label = lv_label_create(screen);
    lv_label_set_text(label, text);
    lv_obj_set_width(label, BSP_LCD_H_RES - 32);
    lv_obj_set_style_text_align(label, LV_TEXT_ALIGN_CENTER, LV_PART_MAIN);
    lv_obj_set_style_text_color(label, lv_color_hex(0x1DB954), LV_PART_MAIN);
    lv_obj_set_style_text_font(label, &lv_font_montserrat_24, LV_PART_MAIN);
    lv_obj_center(label);
}

static void create_portal_screen(const bop_portal_config_t *configuration)
{
    lv_obj_t *screen = prepare_setup_screen();

    lv_obj_t *heading = lv_label_create(screen);
    lv_label_set_text(heading, "Bop WiFi setup");
    lv_obj_set_width(heading, BSP_LCD_H_RES - 32);
    lv_obj_set_pos(heading, 16, 18);
    lv_obj_set_style_text_align(heading, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_style_text_color(heading, lv_color_hex(0x1DB954), 0);
    lv_obj_set_style_text_font(heading, &lv_font_montserrat_24, 0);

    lv_obj_t *credentials_label = lv_label_create(screen);
    lv_label_set_text_fmt(
        credentials_label,
        "Network: %s\nPassword: %s",
        configuration->ap_name,
        configuration->ap_password);
    lv_obj_set_width(credentials_label, BSP_LCD_H_RES - 32);
    lv_obj_set_pos(credentials_label, 16, 60);
    lv_obj_set_style_text_align(credentials_label, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_style_text_color(credentials_label, lv_color_hex(0xFFFFFF), 0);
    lv_obj_set_style_text_font(credentials_label, &lv_font_montserrat_14, 0);

    lv_obj_t *qr_code = lv_qrcode_create(screen);
    lv_qrcode_set_size(qr_code, 210);
    lv_qrcode_set_dark_color(qr_code, lv_color_hex(0x000000));
    lv_qrcode_set_light_color(qr_code, lv_color_hex(0xFFFFFF));
    lv_obj_align(qr_code, LV_ALIGN_TOP_MID, 0, 116);
    if (lv_qrcode_update(
            qr_code,
            configuration->qr_payload,
            strlen(configuration->qr_payload))
        != LV_RESULT_OK) {
        ESP_LOGE(TAG, "Setup QR code creation failed");
    }

    lv_obj_t *caption = lv_label_create(screen);
    lv_label_set_text(caption, "Scan to connect, then follow the setup page");
    lv_obj_set_width(caption, BSP_LCD_H_RES - 40);
    lv_obj_set_pos(caption, 20, 352);
    lv_obj_set_style_text_align(caption, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_style_text_color(caption, lv_color_hex(0xFFFFFF), 0);
    lv_obj_set_style_text_font(caption, &lv_font_montserrat_14, 0);
}

static esp_err_t show_placeholder(const char *text)
{
    if (!bsp_display_lock(DISPLAY_LOCK_FOREVER_MS)) {
        return ESP_ERR_TIMEOUT;
    }
    create_placeholder(text);
    bsp_display_unlock();
    return ESP_OK;
}

static esp_err_t show_portal_screen(void)
{
    if (!bsp_display_lock(DISPLAY_LOCK_FOREVER_MS)) {
        return ESP_ERR_TIMEOUT;
    }
    create_portal_screen(&portal_configuration);
    bsp_display_unlock();
    return ESP_OK;
}

static void startup_task(void *argument)
{
    bop_credentials_t *configured_credentials = argument;
    esp_err_t error = bop_wifi_init();
    if (provision_state == BOP_PROVISION_COMPLETE) {
        if (error == ESP_OK) {
            error = bop_wifi_connect(configured_credentials);
        }
        if (error == ESP_OK) {
            error = bop_time_sync();
        }
        if (error == ESP_OK) {
            error = bop_spotify_start(configured_credentials);
        }
        if (error != ESP_OK) {
            ESP_LOGE(TAG, "Startup failed: %s", esp_err_to_name(error));
        }
        vTaskDelete(NULL);
        return;
    }

    if (error == ESP_OK) {
        error = bop_wifi_connect_bounded(configured_credentials);
    }
    if (error == ESP_ERR_TIMEOUT) {
        error = bop_portal_start(&portal_configuration);
        if (error != ESP_OK) {
            ESP_LOGE(TAG, "Portal start failed: %s", esp_err_to_name(error));
            show_placeholder("WiFi setup failed.\nRestart Bop.");
        } else {
            esp_err_t display_error = show_portal_screen();
            if (display_error != ESP_OK) {
                ESP_LOGE(
                    TAG,
                    "Startup screen update failed: %s",
                    esp_err_to_name(display_error));
            }
        }
        vTaskDelete(NULL);
        return;
    }
    if (error != ESP_OK) {
        ESP_LOGE(TAG, "WiFi startup failed: %s", esp_err_to_name(error));
        show_placeholder("WiFi connection failed.\nRestart Bop.");
        vTaskDelete(NULL);
        return;
    }

    error = bop_time_sync();
    esp_err_t display_error;
    if (error == ESP_OK) {
        display_error = show_placeholder("WiFi ready.\nFinish setup:\nmise run provision");
    } else {
        ESP_LOGE(TAG, "Time synchronization failed: %s", esp_err_to_name(error));
        display_error = show_placeholder(
            "WiFi ready.\nTime sync failed.\nFinish setup:\nmise run provision");
    }
    if (display_error != ESP_OK) {
        ESP_LOGE(TAG, "Startup screen update failed: %s", esp_err_to_name(display_error));
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
    bop_screenshot_mirror_area(area, pixels);
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
    esp_err_t screenshot_error = bop_screenshot_init();
    if (screenshot_error != ESP_OK) {
        ESP_LOGW(TAG, "Screenshot initialization failed: %s", esp_err_to_name(screenshot_error));
    }
    ESP_ERROR_CHECK(bsp_display_brightness_set(85));
    esp_err_t power_error = bop_power_start();
    if (power_error != ESP_OK) {
        ESP_LOGW(TAG, "Battery monitor is unavailable: %s", esp_err_to_name(power_error));
    }

    esp_err_t credentials_error = bop_credentials_init();
    if (credentials_error == ESP_OK) {
        credentials_error = bop_credentials_load(&credentials);
    }
    if (credentials_error == ESP_OK) {
        credentials_error = bop_credentials_state(&credentials, &provision_state);
    }
    if (credentials_error == ESP_OK && provision_state != BOP_PROVISION_COMPLETE) {
        credentials_error = bop_portal_prepare(&portal_configuration);
    }
    const bool provisioned = credentials_error == ESP_OK
        && provision_state == BOP_PROVISION_COMPLETE;

    if (!bsp_display_lock(0)) {
        ESP_LOGE(TAG, "LVGL lock failed");
        return;
    }
    esp_err_t ui_error = ESP_OK;
    if (provisioned) {
        ui_error = bop_ui_start();
    } else if (credentials_error != ESP_OK) {
        create_placeholder("Credential error.\nRun:\nmise run deprovision");
    } else if (provision_state == BOP_PROVISION_NONE) {
        create_portal_screen(&portal_configuration);
    } else if (provision_state == BOP_PROVISION_WIFI_ONLY) {
        create_placeholder("Connecting to WiFi...");
    } else {
        create_placeholder("Credential error.\nRun:\nmise run deprovision");
    }
    // The start runs after an initialization error too, because the serial task
    // answers the host with or without the mirror. bop_screenshot_start refuses
    // on its own when the serial path never came up.
    if (ui_error == ESP_OK) {
        screenshot_error = bop_screenshot_start(display);
        if (screenshot_error != ESP_OK) {
            ESP_LOGW(TAG, "Screenshot task start failed: %s", esp_err_to_name(screenshot_error));
        }
    }
    bsp_display_unlock();

    if (ui_error != ESP_OK) {
        ESP_LOGE(TAG, "UI start failed: %s", esp_err_to_name(ui_error));
        return;
    }
    if (!provisioned) {
        if (credentials_error != ESP_OK) {
            ESP_LOGE(TAG, "Credential state failed: %s", esp_err_to_name(credentials_error));
            return;
        }
        if (provision_state == BOP_PROVISION_NONE) {
            esp_err_t portal_error = bop_portal_start(&portal_configuration);
            if (portal_error != ESP_OK) {
                ESP_LOGE(TAG, "Portal start failed: %s", esp_err_to_name(portal_error));
                show_placeholder("WiFi setup failed.\nRestart Bop.");
            }
            return;
        }
    }
    if (xTaskCreatePinnedToCore(
            startup_task, "bop_startup", 16384, &credentials, 5, NULL, SPOTIFY_TASK_CORE)
        != pdPASS) {
        ESP_LOGE(TAG, "Startup task creation failed");
    }
}
