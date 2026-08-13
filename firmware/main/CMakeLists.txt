# SPDX-FileCopyrightText: 2026 Mark Phelps
# SPDX-License-Identifier: Apache-2.0

idf_component_register(
    SRCS
        "app_main.c"
        "credentials.c"
        "diagnostics.c"
        "power.c"
        "screenshot.c"
        "spotify/spotify.c"
        "ui/art.c"
        "ui/fonts/lv_font_montserrat_bold_24.c"
        "ui/ui.c"
        "wifi.c"
    INCLUDE_DIRS "."
    REQUIRES
        esp32_s3_touch_amoled_1_8
        esp_driver_usb_serial_jtag
        esp_http_client
        esp_netif
        esp_timer
        esp_wifi
        espressif__esp_lcd_touch
        espressif__esp_lvgl_port
        json
        lvgl__lvgl
        mbedtls
        nvs_flash
)

idf_build_get_property(build_components BUILD_COMPONENTS)
if("lvgl__lvgl" IN_LIST build_components)
    idf_component_get_property(LVGL_LIB lvgl__lvgl COMPONENT_LIB)
    if(TARGET ${LVGL_LIB})
        target_compile_options(${LVGL_LIB} PRIVATE -Wno-error=attributes)
    endif()
endif()
