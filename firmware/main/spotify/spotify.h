// SPDX-FileCopyrightText: 2026 Mark Phelps
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "../credentials.h"
#include "esp_err.h"

#define SPOT_TRACK_ID_CAPACITY 64
#define SPOT_TITLE_CAPACITY 256
#define SPOT_ARTISTS_CAPACITY 256
#define SPOT_ALBUM_ART_URL_CAPACITY 512

typedef struct {
    bool available;
    bool is_playing;
    char track_id[SPOT_TRACK_ID_CAPACITY];
    char title[SPOT_TITLE_CAPACITY];
    char artists[SPOT_ARTISTS_CAPACITY];
    char album_art_url[SPOT_ALBUM_ART_URL_CAPACITY];
    uint32_t progress_ms;
    uint32_t duration_ms;
    uint32_t change_counter;
} playback_state_t;

typedef enum {
    SPOTIFY_COMMAND_NEXT,
    SPOTIFY_COMMAND_PREVIOUS,
    SPOTIFY_COMMAND_TOGGLE,
} spotify_command_t;

typedef struct {
    spotify_command_t command;
    uint32_t request_id;
    bool accepted;
    bool was_playing;
} spotify_command_result_t;

esp_err_t spot_spotify_start(spot_credentials_t *credentials);
bool spot_spotify_get_state(playback_state_t *state);
bool spot_spotify_enqueue_command(spotify_command_t command, uint32_t request_id);
bool spot_spotify_get_command_result(spotify_command_result_t *result);
