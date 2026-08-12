// SPDX-FileCopyrightText: 2026 Mark Phelps
// SPDX-License-Identifier: Apache-2.0

#include "art.h"

#include <stdbool.h>
#include <stdlib.h>
#include <string.h>

#include "esp_crt_bundle.h"
#include "esp_heap_caps.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/semphr.h"
#include "jpeg_decoder.h"

#define ART_DOWNLOAD_CAPACITY (1024 * 1024)
#define ART_MAX_DIMENSION 640
#define ART_RENDER_SIZE 368
#define ART_RETRY_DELAY_MS 5000
#define ART_ACCEPT_DELAY_MS 2000
#define ART_TASK_CORE 0

static const char *TAG = "bop_art";
static QueueHandle_t art_result_queue;
static SemaphoreHandle_t art_cache_mutex;
static char active_track_id[BOP_TRACK_ID_CAPACITY];

typedef struct {
    uint8_t *data;
    size_t length;
    size_t capacity;
    bool overflow;
} art_download_t;

static esp_err_t collect_art_data(esp_http_client_event_t *event)
{
    art_download_t *download = event->user_data;
    if (event->event_id != HTTP_EVENT_ON_DATA || event->data_len <= 0) {
        return ESP_OK;
    }
    if (download->length + (size_t)event->data_len > download->capacity) {
        download->overflow = true;
        return ESP_FAIL;
    }
    memcpy(download->data + download->length, event->data, event->data_len);
    download->length += event->data_len;
    return ESP_OK;
}

static esp_err_t download_jpeg(const char *url, uint8_t **jpeg, size_t *jpeg_size)
{
    *jpeg = NULL;
    *jpeg_size = 0;
    if (url == NULL || strncmp(url, "https://", 8) != 0) {
        return ESP_ERR_INVALID_ARG;
    }

    art_download_t download = {
        .data = heap_caps_malloc(
            ART_DOWNLOAD_CAPACITY, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT),
        .capacity = ART_DOWNLOAD_CAPACITY,
    };
    if (download.data == NULL) {
        return ESP_ERR_NO_MEM;
    }

    esp_http_client_config_t configuration = {
        .url = url,
        .event_handler = collect_art_data,
        .user_data = &download,
        .crt_bundle_attach = esp_crt_bundle_attach,
        .timeout_ms = 15000,
        .buffer_size = 4096,
    };
    esp_http_client_handle_t client = esp_http_client_init(&configuration);
    if (client == NULL) {
        free(download.data);
        return ESP_ERR_NO_MEM;
    }
    esp_err_t error = esp_http_client_perform(client);
    int status_code = esp_http_client_get_status_code(client);
    esp_http_client_cleanup(client);
    if (error != ESP_OK || download.overflow || status_code != 200 || download.length == 0) {
        ESP_LOGE(
            TAG,
            "Album art download failed (HTTP %d, %s, %u bytes)",
            status_code,
            esp_err_to_name(error),
            (unsigned)download.length);
        free(download.data);
        return download.overflow ? ESP_ERR_INVALID_SIZE : (error == ESP_OK ? ESP_FAIL : error);
    }

    uint8_t *smaller = heap_caps_realloc(
        download.data,
        download.length,
        MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    *jpeg = smaller != NULL ? smaller : download.data;
    *jpeg_size = download.length;
    return ESP_OK;
}

static uint32_t average_rgb565(const uint16_t *pixels, size_t pixel_count)
{
    uint64_t red = 0;
    uint64_t green = 0;
    uint64_t blue = 0;
    size_t samples = 0;
    for (size_t index = 0; index < pixel_count; index += 8) {
        uint16_t pixel = pixels[index];
        red += ((pixel >> 11) & 0x1f) * 255 / 31;
        green += ((pixel >> 5) & 0x3f) * 255 / 63;
        blue += (pixel & 0x1f) * 255 / 31;
        ++samples;
    }
    if (samples == 0) {
        return 0x282828;
    }
    return ((uint32_t)(red / samples) << 16)
        | ((uint32_t)(green / samples) << 8)
        | (uint32_t)(blue / samples);
}

static void resize_rgb565(
    const uint16_t *source,
    uint32_t source_width,
    uint32_t source_height,
    uint16_t *destination)
{
    memset(destination, 0, ART_RENDER_SIZE * ART_RENDER_SIZE * sizeof(uint16_t));
    uint32_t rendered_width = ART_RENDER_SIZE;
    uint32_t rendered_height = ART_RENDER_SIZE;
    if (source_width > source_height) {
        rendered_height = source_height * ART_RENDER_SIZE / source_width;
    } else if (source_height > source_width) {
        rendered_width = source_width * ART_RENDER_SIZE / source_height;
    }
    uint32_t x_offset = (ART_RENDER_SIZE - rendered_width) / 2;
    uint32_t y_offset = (ART_RENDER_SIZE - rendered_height) / 2;
    for (uint32_t y = 0; y < rendered_height; ++y) {
        uint32_t source_y = y * source_height / rendered_height;
        for (uint32_t x = 0; x < rendered_width; ++x) {
            uint32_t source_x = x * source_width / rendered_width;
            destination[(y + y_offset) * ART_RENDER_SIZE + x + x_offset]
                = source[source_y * source_width + source_x];
        }
    }
}

static bool jpeg_is_bounded(
    const uint8_t *jpeg,
    size_t jpeg_size,
    uint32_t *image_width,
    uint32_t *image_height)
{
    if (jpeg_size < 4 || jpeg[0] != 0xff || jpeg[1] != 0xd8) {
        return false;
    }
    size_t offset = 2;
    bool frame_found = false;
    bool scan_started = false;
    while (offset < jpeg_size) {
        if (jpeg[offset] != 0xff) {
            if (!scan_started) {
                return false;
            }
            ++offset;
            continue;
        }
        while (offset < jpeg_size && jpeg[offset] == 0xff) {
            ++offset;
        }
        if (offset >= jpeg_size) {
            return false;
        }
        uint8_t marker = jpeg[offset++];
        if (scan_started && marker == 0x00) {
            continue;
        }
        if (marker == 0xd9) {
            return scan_started && frame_found;
        }
        if (marker >= 0xd0 && marker <= 0xd7) {
            if (!scan_started) {
                return false;
            }
            continue;
        }
        if (scan_started) {
            return false;
        }
        if (marker == 0xd8 || marker == 0x01) {
            continue;
        }
        if (offset + 2 > jpeg_size) {
            return false;
        }
        size_t segment_length = ((size_t)jpeg[offset] << 8) | jpeg[offset + 1];
        if (segment_length < 2 || segment_length > jpeg_size - offset) {
            return false;
        }
        bool unsupported_frame = (marker >= 0xc1 && marker <= 0xc3)
            || (marker >= 0xc5 && marker <= 0xc7)
            || (marker >= 0xc9 && marker <= 0xcb)
            || (marker >= 0xcd && marker <= 0xcf);
        if (unsupported_frame) {
            return false;
        }
        if (marker == 0xc0) {
            if (frame_found || segment_length < 8) {
                return false;
            }
            uint8_t component_count = jpeg[offset + 7];
            if ((component_count != 1 && component_count != 3)
                || segment_length != 8 + 3 * component_count
                || jpeg[offset + 2] != 8) {
                return false;
            }
            *image_height = ((uint32_t)jpeg[offset + 3] << 8) | jpeg[offset + 4];
            *image_width = ((uint32_t)jpeg[offset + 5] << 8) | jpeg[offset + 6];
            if (*image_width == 0
                || *image_height == 0
                || *image_width > ART_MAX_DIMENSION
                || *image_height > ART_MAX_DIMENSION) {
                return false;
            }
            frame_found = true;
        }
        if (marker == 0xda) {
            if (!frame_found) {
                return false;
            }
            scan_started = true;
        }
        offset += segment_length;
    }
    return false;
}

static esp_err_t decode_jpeg(
    const uint8_t *jpeg,
    size_t jpeg_size,
    const char *track_id,
    bop_album_art_t **decoded_art)
{
    *decoded_art = NULL;
    uint32_t source_width = 0;
    uint32_t source_height = 0;
    if (!jpeg_is_bounded(jpeg, jpeg_size, &source_width, &source_height)) {
        ESP_LOGE(TAG, "Album art JPEG structure is invalid or unsupported");
        return ESP_ERR_INVALID_RESPONSE;
    }
    size_t decoded_size = (size_t)source_width * source_height * sizeof(uint16_t);
    esp_jpeg_image_cfg_t configuration = {
        .indata = (uint8_t *)jpeg,
        .indata_size = jpeg_size,
        .out_format = JPEG_IMAGE_FORMAT_RGB565,
        .out_scale = JPEG_IMAGE_SCALE_0,
        .flags = {
            .swap_color_bytes = 0,
        },
    };
    esp_jpeg_image_output_t output = {0};
    uint8_t *decoded_pixels = heap_caps_malloc(
        decoded_size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (decoded_pixels == NULL) {
        return ESP_ERR_NO_MEM;
    }
    configuration.outbuf = decoded_pixels;
    configuration.outbuf_size = decoded_size;
    esp_err_t error = esp_jpeg_decode(&configuration, &output);
    if (error != ESP_OK) {
        free(decoded_pixels);
        return error;
    }
    if (output.width != source_width
        || output.height != source_height
        || output.output_len != decoded_size) {
        free(decoded_pixels);
        return ESP_ERR_INVALID_RESPONSE;
    }

    bop_album_art_t *art = calloc(1, sizeof(*art));
    if (art == NULL) {
        free(decoded_pixels);
        return ESP_ERR_NO_MEM;
    }
    size_t rendered_size = ART_RENDER_SIZE * ART_RENDER_SIZE * sizeof(uint16_t);
    art->pixels = heap_caps_malloc(
        rendered_size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (art->pixels == NULL) {
        free(decoded_pixels);
        free(art);
        return ESP_ERR_NO_MEM;
    }
    resize_rgb565(
        (const uint16_t *)decoded_pixels,
        output.width,
        output.height,
        (uint16_t *)art->pixels);
    free(decoded_pixels);

    art->image.header.magic = LV_IMAGE_HEADER_MAGIC;
    art->image.header.cf = LV_COLOR_FORMAT_RGB565;
    art->image.header.w = ART_RENDER_SIZE;
    art->image.header.h = ART_RENDER_SIZE;
    art->image.header.stride = ART_RENDER_SIZE * sizeof(uint16_t);
    art->image.data_size = rendered_size;
    art->image.data = art->pixels;
    art->dominant_rgb = average_rgb565(
        (const uint16_t *)art->pixels, ART_RENDER_SIZE * ART_RENDER_SIZE);
    strlcpy(art->track_id, track_id, sizeof(art->track_id));
    *decoded_art = art;
    ESP_LOGI(
        TAG,
        "Album art decoded: %ux%u, %u JPEG bytes",
        output.width,
        output.height,
        (unsigned)jpeg_size);
    return ESP_OK;
}

void bop_album_art_free(bop_album_art_t *art)
{
    if (art == NULL) {
        return;
    }
    free(art->pixels);
    free(art);
}

static void publish_art(bop_album_art_t *art)
{
    bop_album_art_t *stale = NULL;
    if (xQueueReceive(art_result_queue, &stale, 0) == pdTRUE) {
        bop_album_art_free(stale);
    }
    if (xQueueSend(art_result_queue, &art, 0) != pdTRUE) {
        bop_album_art_free(art);
    }
}

static bool art_is_active(const char *track_id)
{
    bool active = false;
    if (xSemaphoreTake(art_cache_mutex, portMAX_DELAY) == pdTRUE) {
        active = strcmp(track_id, active_track_id) == 0;
        xSemaphoreGive(art_cache_mutex);
    }
    return active;
}

void bop_art_mark_active(const char *track_id)
{
    if (track_id == NULL || art_cache_mutex == NULL) {
        return;
    }
    if (xSemaphoreTake(art_cache_mutex, portMAX_DELAY) == pdTRUE) {
        strlcpy(active_track_id, track_id, sizeof(active_track_id));
        xSemaphoreGive(art_cache_mutex);
    }
}

static void art_task(void *argument)
{
    (void)argument;
    char attempted_track[BOP_TRACK_ID_CAPACITY] = {0};
    int64_t retry_after = 0;

    for (;;) {
        playback_state_t state = {0};
        int64_t now = esp_timer_get_time() / 1000;
        bool have_track = bop_spotify_get_state(&state)
            && state.available
            && state.track_id[0] != '\0'
            && state.album_art_url[0] != '\0';
        bool already_cached = have_track && art_is_active(state.track_id);
        bool retry_blocked = have_track
            && strcmp(state.track_id, attempted_track) == 0
            && now < retry_after;
        if (!have_track || already_cached || retry_blocked) {
            vTaskDelay(pdMS_TO_TICKS(250));
            continue;
        }

        strlcpy(attempted_track, state.track_id, sizeof(attempted_track));
        uint8_t *jpeg = NULL;
        size_t jpeg_size = 0;
        esp_err_t error = download_jpeg(state.album_art_url, &jpeg, &jpeg_size);
        bop_album_art_t *art = NULL;
        if (error == ESP_OK) {
            error = decode_jpeg(jpeg, jpeg_size, state.track_id, &art);
        }
        free(jpeg);
        if (error != ESP_OK) {
            ESP_LOGE(TAG, "Album art pipeline failed: %s", esp_err_to_name(error));
            retry_after = esp_timer_get_time() / 1000 + ART_RETRY_DELAY_MS;
            continue;
        }

        playback_state_t latest = {0};
        if (!bop_spotify_get_state(&latest)
            || strcmp(latest.track_id, art->track_id) != 0) {
            bop_album_art_free(art);
            continue;
        }
        retry_after = esp_timer_get_time() / 1000 + ART_ACCEPT_DELAY_MS;
        publish_art(art);
    }
}

esp_err_t bop_art_pipeline_start(QueueHandle_t result_queue)
{
    if (result_queue == NULL || art_result_queue != NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    art_cache_mutex = xSemaphoreCreateMutex();
    if (art_cache_mutex == NULL) {
        return ESP_ERR_NO_MEM;
    }
    active_track_id[0] = '\0';
    art_result_queue = result_queue;
    if (xTaskCreatePinnedToCore(
            art_task, "album_art", 8192, NULL, 4, NULL, ART_TASK_CORE)
        != pdPASS) {
        art_result_queue = NULL;
        vSemaphoreDelete(art_cache_mutex);
        art_cache_mutex = NULL;
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}
