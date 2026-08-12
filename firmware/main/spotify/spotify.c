// SPDX-FileCopyrightText: 2026 Mark Phelps
// SPDX-License-Identifier: Apache-2.0

#include "spotify.h"

#include <ctype.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

#include "cJSON.h"
#include "esp_crt_bundle.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "mbedtls/platform_util.h"
#include "../wifi.h"

#define ACCESS_TOKEN_CAPACITY 2048
#define TOKEN_RESPONSE_CAPACITY 8192
#define PLAYBACK_RESPONSE_CAPACITY 32768
#define COMMAND_QUEUE_LENGTH 8
#define POLL_INTERVAL_MS 2000
#define TOKEN_REFRESH_MARGIN_SECONDS 60
#define SPOTIFY_TASK_CORE 0

static const char *TAG = "spot_spotify";

typedef struct {
    char *data;
    size_t length;
    size_t capacity;
    int retry_after_seconds;
    bool overflow;
} response_buffer_t;

typedef struct {
    spot_credentials_t *credentials;
    char access_token[ACCESS_TOKEN_CAPACITY];
    int64_t access_token_expires_at;
    response_buffer_t response;
    esp_http_client_handle_t api_client;
} client_context_t;

typedef struct {
    spotify_command_t command;
    uint32_t request_id;
} spotify_command_request_t;

static SemaphoreHandle_t state_mutex;
static QueueHandle_t command_queue;
static QueueHandle_t command_result_queue;
static playback_state_t current_state;
static bool state_ready;
static bool client_started;

static esp_err_t collect_response(esp_http_client_event_t *event)
{
    response_buffer_t *response = event->user_data;
    if (event->event_id == HTTP_EVENT_ON_HEADER
        && event->header_key != NULL
        && event->header_value != NULL
        && strcasecmp(event->header_key, "Retry-After") == 0) {
        char *end = NULL;
        long seconds = strtol(event->header_value, &end, 10);
        if (end != event->header_value && seconds > 0 && seconds <= 3600) {
            response->retry_after_seconds = (int)seconds;
        }
        return ESP_OK;
    }
    if (event->event_id != HTTP_EVENT_ON_DATA || event->data_len <= 0) {
        return ESP_OK;
    }
    if (response->length + (size_t)event->data_len >= response->capacity) {
        response->overflow = true;
        return ESP_FAIL;
    }
    memcpy(response->data + response->length, event->data, event->data_len);
    response->length += event->data_len;
    response->data[response->length] = '\0';
    return ESP_OK;
}

static void secure_free(char *data, size_t capacity)
{
    if (data != NULL) {
        mbedtls_platform_zeroize(data, capacity);
        free(data);
    }
}

static void secure_free_string(char *data)
{
    if (data != NULL) {
        secure_free(data, strlen(data) + 1);
    }
}

static void delete_token_response(cJSON *root)
{
    if (root == NULL) {
        return;
    }
    cJSON *access = cJSON_GetObjectItemCaseSensitive(root, "access_token");
    cJSON *refresh = cJSON_GetObjectItemCaseSensitive(root, "refresh_token");
    if (cJSON_IsString(access) && access->valuestring != NULL) {
        mbedtls_platform_zeroize(access->valuestring, strlen(access->valuestring));
    }
    if (cJSON_IsString(refresh) && refresh->valuestring != NULL) {
        mbedtls_platform_zeroize(refresh->valuestring, strlen(refresh->valuestring));
    }
    cJSON_Delete(root);
}

static bool is_form_safe(unsigned char character)
{
    return isalnum(character) || character == '-' || character == '.' || character == '_' || character == '~';
}

static char *form_encode(const char *value)
{
    size_t length = strlen(value);
    char *encoded = malloc(length * 3 + 1);
    if (encoded == NULL) {
        return NULL;
    }

    char *output = encoded;
    static const char hexadecimal[] = "0123456789ABCDEF";
    for (const unsigned char *input = (const unsigned char *)value; *input != '\0'; ++input) {
        if (is_form_safe(*input)) {
            *output++ = (char)*input;
        } else {
            *output++ = '%';
            *output++ = hexadecimal[*input >> 4];
            *output++ = hexadecimal[*input & 0x0f];
        }
    }
    *output = '\0';
    return encoded;
}

static esp_err_t perform_request(
    esp_http_client_handle_t client, response_buffer_t *response, int *status_code)
{
    response->length = 0;
    response->retry_after_seconds = 0;
    response->overflow = false;
    response->data[0] = '\0';
    esp_err_t error = esp_http_client_perform(client);
    *status_code = esp_http_client_get_status_code(client);
    if (response->overflow) {
        return ESP_ERR_INVALID_SIZE;
    }
    return error;
}

static esp_err_t refresh_access_token(client_context_t *context, int *retry_after_seconds)
{
    *retry_after_seconds = 0;
    char *encoded_refresh_token = form_encode(context->credentials->refresh_token);
    char *encoded_client_id = form_encode(context->credentials->client_id);
    char *response_data = malloc(TOKEN_RESPONSE_CAPACITY);
    if (encoded_refresh_token == NULL || encoded_client_id == NULL || response_data == NULL) {
        secure_free_string(encoded_refresh_token);
        secure_free_string(encoded_client_id);
        secure_free(response_data, TOKEN_RESPONSE_CAPACITY);
        return ESP_ERR_NO_MEM;
    }

    size_t payload_capacity = strlen(encoded_refresh_token) + strlen(encoded_client_id) + 64;
    char *payload = malloc(payload_capacity);
    if (payload == NULL) {
        secure_free_string(encoded_refresh_token);
        secure_free_string(encoded_client_id);
        secure_free(response_data, TOKEN_RESPONSE_CAPACITY);
        return ESP_ERR_NO_MEM;
    }
    snprintf(
        payload,
        payload_capacity,
        "grant_type=refresh_token&refresh_token=%s&client_id=%s",
        encoded_refresh_token,
        encoded_client_id);
    secure_free_string(encoded_refresh_token);
    secure_free_string(encoded_client_id);

    response_buffer_t response = {
        .data = response_data,
        .capacity = TOKEN_RESPONSE_CAPACITY,
    };
    esp_http_client_config_t configuration = {
        .url = "https://accounts.spotify.com/api/token",
        .method = HTTP_METHOD_POST,
        .event_handler = collect_response,
        .user_data = &response,
        .crt_bundle_attach = esp_crt_bundle_attach,
        .timeout_ms = 15000,
    };
    esp_http_client_handle_t client = esp_http_client_init(&configuration);
    if (client == NULL) {
        secure_free(payload, payload_capacity);
        secure_free(response_data, TOKEN_RESPONSE_CAPACITY);
        return ESP_ERR_NO_MEM;
    }

    esp_err_t error = esp_http_client_set_header(
        client, "Content-Type", "application/x-www-form-urlencoded");
    if (error == ESP_OK) {
        error = esp_http_client_set_post_field(client, payload, strlen(payload));
    }
    int status_code = 0;
    if (error == ESP_OK) {
        error = perform_request(client, &response, &status_code);
    }
    esp_http_client_cleanup(client);
    secure_free(payload, payload_capacity);
    *retry_after_seconds = response.retry_after_seconds;
    if (status_code == 429) {
        if (*retry_after_seconds <= 0) {
            *retry_after_seconds = 1;
        }
        ESP_LOGW(TAG, "Token refresh rate limit; waiting %d seconds", *retry_after_seconds);
        secure_free(response_data, TOKEN_RESPONSE_CAPACITY);
        return ESP_ERR_TIMEOUT;
    }
    if (error != ESP_OK || status_code != 200) {
        ESP_LOGE(TAG, "Access token refresh failed (HTTP %d, %s)", status_code, esp_err_to_name(error));
        secure_free(response_data, TOKEN_RESPONSE_CAPACITY);
        return error == ESP_OK ? ESP_FAIL : error;
    }

    cJSON *root = cJSON_Parse(response_data);
    secure_free(response_data, TOKEN_RESPONSE_CAPACITY);
    if (root == NULL) {
        return ESP_ERR_INVALID_RESPONSE;
    }
    cJSON *access = cJSON_GetObjectItemCaseSensitive(root, "access_token");
    if (!cJSON_IsString(access)
        || access->valuestring == NULL
        || strlen(access->valuestring) >= sizeof(context->access_token)) {
        delete_token_response(root);
        return ESP_ERR_INVALID_SIZE;
    }

    cJSON *rotated = cJSON_GetObjectItemCaseSensitive(root, "refresh_token");
    if (cJSON_IsString(rotated) && rotated->valuestring != NULL && rotated->valuestring[0] != '\0') {
        if (strlen(rotated->valuestring) >= sizeof(context->credentials->refresh_token)) {
            delete_token_response(root);
            return ESP_ERR_INVALID_SIZE;
        }
        error = spot_credentials_store_refresh_token(rotated->valuestring);
        if (error != ESP_OK) {
            delete_token_response(root);
            return error;
        }
        mbedtls_platform_zeroize(
            context->credentials->refresh_token, sizeof(context->credentials->refresh_token));
        strlcpy(
            context->credentials->refresh_token,
            rotated->valuestring,
            sizeof(context->credentials->refresh_token));
        ESP_LOGI(TAG, "Stored a rotated refresh token");
    }

    mbedtls_platform_zeroize(context->access_token, sizeof(context->access_token));
    strlcpy(context->access_token, access->valuestring, sizeof(context->access_token));
    cJSON *expires = cJSON_GetObjectItemCaseSensitive(root, "expires_in");
    int expires_in = cJSON_IsNumber(expires) && expires->valueint > 0 ? expires->valueint : 3600;
    context->access_token_expires_at = esp_timer_get_time() / 1000000 + expires_in;
    delete_token_response(root);
    ESP_LOGI(TAG, "Access token obtained");
    return ESP_OK;
}

static esp_err_t ensure_access_token(
    client_context_t *context, bool force_refresh, int *retry_after_seconds)
{
    int64_t now = esp_timer_get_time() / 1000000;
    bool expiring = context->access_token_expires_at - now <= TOKEN_REFRESH_MARGIN_SECONDS;
    if (force_refresh || context->access_token[0] == '\0' || expiring) {
        return refresh_access_token(context, retry_after_seconds);
    }
    *retry_after_seconds = 0;
    return ESP_OK;
}

static esp_err_t set_authorization_header(client_context_t *context)
{
    size_t capacity = strlen(context->access_token) + sizeof("Bearer ");
    char *authorization = malloc(capacity);
    if (authorization == NULL) {
        return ESP_ERR_NO_MEM;
    }
    snprintf(authorization, capacity, "Bearer %s", context->access_token);
    esp_err_t error = esp_http_client_set_header(
        context->api_client, "Authorization", authorization);
    secure_free(authorization, capacity);
    return error;
}

static esp_err_t api_request(
    client_context_t *context,
    const char *url,
    esp_http_client_method_t method,
    int *status_code,
    int *retry_after_seconds)
{
    *retry_after_seconds = 0;
    for (int attempt = 0; attempt < 2; ++attempt) {
        esp_err_t error = ensure_access_token(context, attempt > 0, retry_after_seconds);
        if (error != ESP_OK) {
            return error;
        }
        error = esp_http_client_set_url(context->api_client, url);
        if (error == ESP_OK) {
            error = esp_http_client_set_method(context->api_client, method);
        }
        if (error == ESP_OK) {
            error = set_authorization_header(context);
        }
        if (error == ESP_OK) {
            error = perform_request(context->api_client, &context->response, status_code);
        }
        *retry_after_seconds = context->response.retry_after_seconds;
        if (error != ESP_OK || *status_code != 401) {
            return error;
        }
        ESP_LOGW(TAG, "Spotify rejected the access token; refreshing it once");
        mbedtls_platform_zeroize(context->access_token, sizeof(context->access_token));
        context->access_token_expires_at = 0;
    }
    return ESP_FAIL;
}

static bool playback_state_equal(const playback_state_t *left, const playback_state_t *right)
{
    return left->available == right->available
        && left->is_playing == right->is_playing
        && left->progress_ms == right->progress_ms
        && left->duration_ms == right->duration_ms
        && strcmp(left->track_id, right->track_id) == 0
        && strcmp(left->title, right->title) == 0
        && strcmp(left->artists, right->artists) == 0
        && strcmp(left->album_art_url, right->album_art_url) == 0;
}

static void publish_state(playback_state_t *next)
{
    xSemaphoreTake(state_mutex, portMAX_DELAY);
    playback_state_t previous = current_state;
    bool first_state = !state_ready;
    bool changed = first_state || !playback_state_equal(&previous, next);
    if (changed) {
        next->change_counter = previous.change_counter + 1;
        current_state = *next;
        state_ready = true;
    }
    xSemaphoreGive(state_mutex);

    if (!changed) {
        return;
    }
    if (!next->available && (first_state || previous.available)) {
        ESP_LOGI(TAG, "Playback state: nothing is playing");
        return;
    }
    if (next->available
        && (first_state || !previous.available || strcmp(previous.track_id, next->track_id) != 0)) {
        ESP_LOGI(TAG, "Playback state: %s — %s", next->title, next->artists);
    }
    if (next->available
        && (!previous.available || previous.is_playing != next->is_playing)) {
        ESP_LOGI(TAG, "Playback is %s", next->is_playing ? "playing" : "paused");
    }
}

static void append_artist(char *artists, size_t capacity, const char *name)
{
    size_t used = strlen(artists);
    if (used > 0 && used + 2 < capacity) {
        strlcpy(artists + used, ", ", capacity - used);
        used += 2;
    }
    if (used < capacity) {
        strlcpy(artists + used, name, capacity - used);
    }
}

static uint32_t json_uint32(cJSON *value)
{
    if (!cJSON_IsNumber(value) || value->valuedouble <= 0) {
        return 0;
    }
    if (value->valuedouble >= UINT32_MAX) {
        return UINT32_MAX;
    }
    return (uint32_t)value->valuedouble;
}

static esp_err_t parse_playback(const char *json, playback_state_t *state)
{
    cJSON *root = cJSON_Parse(json);
    if (root == NULL) {
        return ESP_ERR_INVALID_RESPONSE;
    }
    cJSON *item = cJSON_GetObjectItemCaseSensitive(root, "item");
    if (cJSON_IsNull(item)) {
        *state = (playback_state_t){0};
        cJSON_Delete(root);
        return ESP_OK;
    }
    cJSON *title = cJSON_IsObject(item) ? cJSON_GetObjectItemCaseSensitive(item, "name") : NULL;
    if (!cJSON_IsString(title) || title->valuestring == NULL) {
        cJSON_Delete(root);
        return ESP_ERR_INVALID_RESPONSE;
    }

    *state = (playback_state_t){.available = true};
    strlcpy(state->title, title->valuestring, sizeof(state->title));
    cJSON *track_id = cJSON_GetObjectItemCaseSensitive(item, "id");
    if (cJSON_IsString(track_id) && track_id->valuestring != NULL) {
        strlcpy(state->track_id, track_id->valuestring, sizeof(state->track_id));
    }

    cJSON *artists = cJSON_GetObjectItemCaseSensitive(item, "artists");
    cJSON *artist = NULL;
    cJSON_ArrayForEach(artist, artists) {
        cJSON *name = cJSON_GetObjectItemCaseSensitive(artist, "name");
        if (cJSON_IsString(name) && name->valuestring != NULL) {
            append_artist(state->artists, sizeof(state->artists), name->valuestring);
        }
    }

    cJSON *album = cJSON_GetObjectItemCaseSensitive(item, "album");
    cJSON *images = cJSON_IsObject(album) ? cJSON_GetObjectItemCaseSensitive(album, "images") : NULL;
    cJSON *fallback_image = cJSON_IsArray(images) ? cJSON_GetArrayItem(images, 0) : NULL;
    cJSON *image = NULL;
    cJSON_ArrayForEach(image, images) {
        cJSON *width = cJSON_GetObjectItemCaseSensitive(image, "width");
        if (cJSON_IsNumber(width) && width->valueint == 300) {
            fallback_image = image;
            break;
        }
    }
    cJSON *image_url = cJSON_IsObject(fallback_image)
        ? cJSON_GetObjectItemCaseSensitive(fallback_image, "url")
        : NULL;
    if (cJSON_IsString(image_url) && image_url->valuestring != NULL) {
        strlcpy(state->album_art_url, image_url->valuestring, sizeof(state->album_art_url));
    }

    cJSON *progress = cJSON_GetObjectItemCaseSensitive(root, "progress_ms");
    cJSON *duration = cJSON_GetObjectItemCaseSensitive(item, "duration_ms");
    cJSON *is_playing = cJSON_GetObjectItemCaseSensitive(root, "is_playing");
    state->progress_ms = json_uint32(progress);
    state->duration_ms = json_uint32(duration);
    state->is_playing = cJSON_IsTrue(is_playing);
    cJSON_Delete(root);
    return ESP_OK;
}

static esp_err_t poll_current_playback(client_context_t *context, int *backoff_seconds)
{
    int status_code = 0;
    esp_err_t error = api_request(
        context,
        "https://api.spotify.com/v1/me/player/currently-playing",
        HTTP_METHOD_GET,
        &status_code,
        backoff_seconds);
    if (error != ESP_OK) {
        ESP_LOGE(TAG, "Playback poll failed: %s", esp_err_to_name(error));
        return error;
    }
    if (status_code == 429) {
        if (*backoff_seconds <= 0) {
            *backoff_seconds = 1;
        }
        ESP_LOGW(TAG, "Spotify rate limit; waiting %d seconds", *backoff_seconds);
        return ESP_OK;
    }
    if (status_code == 204) {
        playback_state_t empty = {0};
        publish_state(&empty);
        return ESP_OK;
    }
    if (status_code != 200) {
        ESP_LOGE(TAG, "Playback poll failed (HTTP %d)", status_code);
        return ESP_FAIL;
    }

    playback_state_t next = {0};
    error = parse_playback(context->response.data, &next);
    if (error == ESP_OK) {
        publish_state(&next);
    } else {
        ESP_LOGE(TAG, "Playback response is invalid");
    }
    return error;
}

static const char *command_name(spotify_command_t command, bool is_playing)
{
    if (command == SPOTIFY_COMMAND_NEXT) {
        return "next";
    }
    if (command == SPOTIFY_COMMAND_PREVIOUS) {
        return "previous";
    }
    return is_playing ? "pause" : "play";
}

static esp_err_t send_command(
    client_context_t *context,
    spotify_command_t command,
    int *backoff_seconds,
    bool *was_playing)
{
    playback_state_t state = {0};
    spot_spotify_get_state(&state);
    *was_playing = state.is_playing;
    const char *url = NULL;
    esp_http_client_method_t method = HTTP_METHOD_POST;
    if (command == SPOTIFY_COMMAND_NEXT) {
        url = "https://api.spotify.com/v1/me/player/next";
    } else if (command == SPOTIFY_COMMAND_PREVIOUS) {
        url = "https://api.spotify.com/v1/me/player/previous";
    } else if (state.is_playing) {
        url = "https://api.spotify.com/v1/me/player/pause";
        method = HTTP_METHOD_PUT;
    } else {
        url = "https://api.spotify.com/v1/me/player/play";
        method = HTTP_METHOD_PUT;
    }

    const char *name = command_name(command, state.is_playing);
    ESP_LOGI(TAG, "Sending %s command", name);
    int status_code = 0;
    esp_err_t error = api_request(
        context, url, method, &status_code, backoff_seconds);
    if (error != ESP_OK) {
        ESP_LOGE(TAG, "%s command failed: %s", name, esp_err_to_name(error));
        return error;
    }
    if (status_code == 429) {
        if (*backoff_seconds <= 0) {
            *backoff_seconds = 1;
        }
        ESP_LOGW(TAG, "Spotify rate limit; waiting %d seconds", *backoff_seconds);
        return ESP_OK;
    }
    if (status_code < 200 || status_code >= 300) {
        ESP_LOGE(TAG, "%s command failed (HTTP %d)", name, status_code);
        return ESP_FAIL;
    }
    ESP_LOGI(TAG, "%s command accepted", name);
    return ESP_OK;
}

static void publish_command_result(
    const spotify_command_request_t *request, bool accepted, bool was_playing)
{
    if (request->request_id == 0) {
        return;
    }
    spotify_command_result_t result = {
        .command = request->command,
        .request_id = request->request_id,
        .accepted = accepted,
        .was_playing = was_playing,
    };
    if (xQueueSend(command_result_queue, &result, 0) != pdTRUE) {
        ESP_LOGW(TAG, "Spotify command result queue is full");
    }
}

static TickType_t ticks_until(TickType_t deadline)
{
    TickType_t now = xTaskGetTickCount();
    return (int32_t)(deadline - now) > 0 ? deadline - now : 0;
}

static TickType_t delay_from_seconds(int seconds)
{
    return pdMS_TO_TICKS((uint32_t)seconds * 1000);
}

static client_context_t *create_client_context(spot_credentials_t *credentials)
{
    client_context_t *context = malloc(sizeof(*context));
    if (context == NULL) {
        return NULL;
    }
    mbedtls_platform_zeroize(context, sizeof(*context));
    context->credentials = credentials;
    context->response.data = malloc(PLAYBACK_RESPONSE_CAPACITY);
    context->response.capacity = PLAYBACK_RESPONSE_CAPACITY;
    if (context->response.data == NULL) {
        secure_free((char *)context, sizeof(*context));
        return NULL;
    }

    esp_http_client_config_t configuration = {
        .url = "https://api.spotify.com/v1/me/player/currently-playing",
        .event_handler = collect_response,
        .user_data = &context->response,
        .crt_bundle_attach = esp_crt_bundle_attach,
        .timeout_ms = 15000,
        .keep_alive_enable = true,
        .keep_alive_idle = 5,
        .keep_alive_interval = 5,
        .keep_alive_count = 3,
    };
    context->api_client = esp_http_client_init(&configuration);
    if (context->api_client == NULL) {
        free(context->response.data);
        secure_free((char *)context, sizeof(*context));
        return NULL;
    }
    return context;
}

static void destroy_client_context(client_context_t *context)
{
    if (context == NULL) {
        return;
    }
    if (context->api_client != NULL) {
        esp_http_client_cleanup(context->api_client);
    }
    free(context->response.data);
    secure_free((char *)context, sizeof(*context));
}

static void client_task(void *argument)
{
    client_context_t *context = argument;
    TickType_t poll_deadline = xTaskGetTickCount();
    TickType_t rate_limit_deadline = 0;
    spotify_command_request_t pending_command = {0};
    bool command_pending = false;

    for (;;) {
        if (rate_limit_deadline != 0) {
            TickType_t wait = ticks_until(rate_limit_deadline);
            if (wait > 0) {
                vTaskDelay(wait);
            }
            rate_limit_deadline = 0;
        }

        spot_wifi_wait_connected();
        if (!command_pending) {
            TickType_t wait = ticks_until(poll_deadline);
            command_pending = xQueueReceive(command_queue, &pending_command, wait) == pdTRUE;
        }
        spot_wifi_wait_connected();

        int backoff_seconds = 0;
        if (command_pending) {
            bool was_playing = false;
            esp_err_t command_error = send_command(
                context, pending_command.command, &backoff_seconds, &was_playing);
            if (backoff_seconds > 0) {
                rate_limit_deadline = xTaskGetTickCount() + delay_from_seconds(backoff_seconds);
                poll_deadline = rate_limit_deadline;
                continue;
            }
            command_pending = false;
            publish_command_result(&pending_command, command_error == ESP_OK, was_playing);
            if (command_error != ESP_OK) {
                ESP_LOGW(TAG, "Command was not applied; refreshing playback state");
            }
        }

        spot_wifi_wait_connected();
        TickType_t poll_started = xTaskGetTickCount();
        poll_current_playback(context, &backoff_seconds);
        poll_deadline = poll_started + pdMS_TO_TICKS(POLL_INTERVAL_MS);
        if (backoff_seconds > 0) {
            rate_limit_deadline = xTaskGetTickCount() + delay_from_seconds(backoff_seconds);
            poll_deadline = rate_limit_deadline;
        }
    }
}

static void debug_console_task(void *argument)
{
    (void)argument;
    ESP_LOGI(TAG, "Serial controls: n=next, b=previous, t=toggle");
    for (;;) {
        int input = getchar();
        if (input == EOF) {
            clearerr(stdin);
            vTaskDelay(pdMS_TO_TICKS(50));
            continue;
        }
        spotify_command_t command;
        if (input == 'n' || input == 'N') {
            command = SPOTIFY_COMMAND_NEXT;
        } else if (input == 'b' || input == 'B') {
            command = SPOTIFY_COMMAND_PREVIOUS;
        } else if (input == 't' || input == 'T') {
            command = SPOTIFY_COMMAND_TOGGLE;
        } else {
            continue;
        }
        if (!spot_spotify_enqueue_command(command, 0)) {
            ESP_LOGW(TAG, "Command queue is full");
        }
    }
}

esp_err_t spot_spotify_start(spot_credentials_t *credentials)
{
    if (credentials == NULL || client_started) {
        return ESP_ERR_INVALID_STATE;
    }
    state_mutex = xSemaphoreCreateMutex();
    command_queue = xQueueCreate(COMMAND_QUEUE_LENGTH, sizeof(spotify_command_request_t));
    command_result_queue = xQueueCreate(COMMAND_QUEUE_LENGTH, sizeof(spotify_command_result_t));
    if (state_mutex == NULL || command_queue == NULL || command_result_queue == NULL) {
        if (state_mutex != NULL) {
            vSemaphoreDelete(state_mutex);
            state_mutex = NULL;
        }
        if (command_queue != NULL) {
            vQueueDelete(command_queue);
            command_queue = NULL;
        }
        if (command_result_queue != NULL) {
            vQueueDelete(command_result_queue);
            command_result_queue = NULL;
        }
        return ESP_ERR_NO_MEM;
    }

    client_context_t *context = create_client_context(credentials);
    if (context == NULL) {
        vSemaphoreDelete(state_mutex);
        vQueueDelete(command_queue);
        vQueueDelete(command_result_queue);
        state_mutex = NULL;
        command_queue = NULL;
        command_result_queue = NULL;
        return ESP_ERR_NO_MEM;
    }
    mbedtls_platform_zeroize(&current_state, sizeof(current_state));
    state_ready = false;
    if (xTaskCreatePinnedToCore(
            client_task, "spotify_client", 16384, context, 5, NULL, SPOTIFY_TASK_CORE)
        != pdPASS) {
        destroy_client_context(context);
        vSemaphoreDelete(state_mutex);
        vQueueDelete(command_queue);
        vQueueDelete(command_result_queue);
        state_mutex = NULL;
        command_queue = NULL;
        command_result_queue = NULL;
        return ESP_ERR_NO_MEM;
    }
    client_started = true;
    if (xTaskCreatePinnedToCore(
            debug_console_task, "spotify_console", 3072, NULL, 2, NULL, SPOTIFY_TASK_CORE)
        != pdPASS) {
        ESP_LOGW(TAG, "Serial command task creation failed");
    }
    return ESP_OK;
}

bool spot_spotify_get_state(playback_state_t *state)
{
    if (state == NULL || state_mutex == NULL) {
        return false;
    }
    xSemaphoreTake(state_mutex, portMAX_DELAY);
    *state = current_state;
    bool ready = state_ready;
    xSemaphoreGive(state_mutex);
    return ready;
}

bool spot_spotify_enqueue_command(spotify_command_t command, uint32_t request_id)
{
    if (command_queue == NULL) {
        return false;
    }
    spotify_command_request_t request = {
        .command = command,
        .request_id = request_id,
    };
    return xQueueSend(command_queue, &request, 0) == pdTRUE;
}

bool spot_spotify_get_command_result(spotify_command_result_t *result)
{
    if (result == NULL || command_result_queue == NULL) {
        return false;
    }
    return xQueueReceive(command_result_queue, result, 0) == pdTRUE;
}
