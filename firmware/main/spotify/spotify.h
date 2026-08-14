// SPDX-FileCopyrightText: 2026 Mark Phelps
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "../credentials.h"
#include "esp_err.h"

#define BOP_TRACK_ID_CAPACITY 64
#define BOP_TITLE_CAPACITY 256
#define BOP_ARTISTS_CAPACITY 256
#define BOP_ALBUM_ART_URL_CAPACITY 512

typedef struct {
    bool available;
    bool is_playing;
    char track_id[BOP_TRACK_ID_CAPACITY];
    char title[BOP_TITLE_CAPACITY];
    char artists[BOP_ARTISTS_CAPACITY];
    char album_art_url[BOP_ALBUM_ART_URL_CAPACITY];
    uint32_t progress_ms;
    uint32_t duration_ms;
    uint8_t volume_percent;
    bool volume_available;
    uint32_t volume_command_generation;
    uint32_t change_counter;
} playback_state_t;

typedef enum {
    SPOTIFY_COMMAND_NEXT,
    SPOTIFY_COMMAND_PREVIOUS,
    SPOTIFY_COMMAND_TOGGLE,
    SPOTIFY_COMMAND_SET_VOLUME,
} spotify_command_t;

typedef enum {
    SPOTIFY_COMMAND_SUBMISSION_NOT_READY,
    SPOTIFY_COMMAND_SUBMISSION_QUEUE_FULL,
    SPOTIFY_COMMAND_SUBMISSION_RATE_LIMITED,
    SPOTIFY_COMMAND_SUBMISSION_QUEUED,
} spotify_command_submission_t;

typedef struct {
    spotify_command_t command;
    uint32_t request_id;
    bool accepted;
    bool rate_limited;
    bool was_playing;
} spotify_command_result_t;

esp_err_t bop_spotify_start(bop_credentials_t *credentials);
bool bop_spotify_get_state(playback_state_t *state);
bool bop_spotify_commands_ready(void);
spotify_command_submission_t bop_spotify_enqueue_command(
    spotify_command_t command, uint32_t request_id);
bool bop_spotify_enqueue_volume(uint8_t volume_percent);
uint32_t bop_spotify_get_volume_request_generation(void);
bool bop_spotify_get_command_result(spotify_command_result_t *result);
