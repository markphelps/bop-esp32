# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Bop is a Spotify remote for the Waveshare ESP32-S3 Touch AMOLED 1.8 board. The
board shows the current track and sends touch commands to the Spotify Web API.
It plays no audio.

The repository holds two halves:

- `firmware/` — C firmware for ESP-IDF 5.5, built with `idf.py`.
- `tools/` — host-side Python that installs the toolchain, backs up flash,
  provisions credentials, and runs the host checks. `mise` starts every tool.

One name runs through every layer. The product is "Bop", the repository is
`bop-esp32`, the ESP-IDF project is `bop`, and every environment variable Bop
defines starts with `BOP_`. Public firmware functions use the `bop_` prefix,
and the capacity macros use `BOP_`. The name Spotify is the one exception, and
it stays wherever it means Spotify.

## Commands

```
mise install            # install Python, esptool, gitleaks, ruff, cmake, ninja, ESP-IDF
mise run build          # build firmware into firmware/build
mise run flash          # flash the firmware (needs a factory backup first)
mise run monitor        # open the serial monitor
mise run test-host      # run every host check, no board necessary
mise run format         # Ruff format over every Python file
mise run format-check   # Ruff format check that changes no file
mise run lint           # Ruff lint
mise run licenses       # REUSE lint: every file needs a license and a holder
mise run secrets        # gitleaks over tracked files and all branches and tags
```

Run one host check alone with `python tools/test_provision.py`. Each check is a
plain script with a `main()` function and bare `assert` statements. There is no
pytest, and there are no third-party test dependencies.

The performance build is a second build directory, not a flag. `BOP_PERF_MONITOR=1`
makes `tools/run_idf.py` add `sdkconfig.perf.defaults` and use `firmware/build-perf`:
use `mise run build-perf`, `flash-perf`, and `monitor-perf`.

Set `BOP_PORT` when more than one Espressif board is connected. Set
`BOP_IDF_PATH` to move the ESP-IDF checkout away from `~/.local/share/esp-idf`.
`BOP_SKIP_IDF=1` makes the `mise install` hook skip the ESP-IDF install. CI sets
it for every job, because no job there installs ESP-IDF from this checkout. In
that environment, no firmware build is possible.

The firmware needs the real board. There is no host build and no emulator, so
`mise run test-host`, the firmware compile, and a careful read are the only
checks available without hardware.

## Continuous integration

`.github/workflows/ci.yml` is the only workflow. It runs five required checks on
each pull request: `firmware`, `host-tools (linux)`, `python-style`, `secrets`,
and `licenses`. Each host job runs the matching local `mise` task, from the
versions that `mise.toml` pins. The `firmware` job is the exception: it runs
`idf.py` inside the official ESP-IDF container, whose release must stay equal to
`IDF_TAG` in `tools/setup_idf.py`. CI is Linux only. macOS is tested locally on
the board, and Windows is not tested.

## Firmware architecture

`app_main` (`firmware/main/app_main.c`) starts the display first, then reads the
credentials, then starts the rest. If NVS holds no credentials, the app shows a
"run: mise run provision" screen and starts nothing else.

Work is divided across the two cores:

- Core 1 runs the LVGL task and the 250 ms `ui_timer` in `ui/ui.c`.
- Core 0 runs WiFi, the Spotify client task, the album-art task, and the
  one-minute soak diagnostics task.

Data moves one way. The Spotify task polls `currently-playing` every two
seconds and publishes a `playback_state_t` snapshot behind a mutex. Each real
change increments `change_counter`. The UI timer copies that snapshot with
`bop_spotify_get_state` and redraws only when the counter moves.

Commands move back through queues, never through direct calls. A gesture calls
`bop_spotify_enqueue_command`, the client task sends the request, and the
result returns on a second queue that `process_command_results` drains. The UI
shows the new play state immediately, then the next snapshot corrects it. The
`optimistic_*` fields in `ui_context_t` hold that short window.

The art pipeline (`ui/art.c`) is a third producer. It downloads the JPEG into
PSRAM, decodes it with `esp_jpeg`, scales it to 368 pixels, and averages the
pixels for the background color. It passes one `bop_album_art_t *` through a
one-slot queue. The UI owns that pointer after it arrives and frees the old one
with `bop_album_art_free`.

Rules that the structure depends on:

- Every LVGL call outside the LVGL task must hold the lock
  (`bsp_display_lock` or `lvgl_port_lock`). Code inside `ui_timer` and inside
  LVGL event callbacks already runs in that task, so it must not lock again.
- `flush_display` sends a QSPI NOP after each color transfer. That parameter
  transfer is what makes DMA complete before LVGL reuses the buffer. Do not
  remove it.
- `start_display` calls `recover_display_panel` before `lvgl_port_add_disp`.
  This off-reset-init-on sequence recovers the AMOLED panel after a warm reset.
- `round_display_area` aligns every invalidated area to even pixels, because
  the panel needs it.

## Credentials and secrets

The four NVS keys are `wifi_ssid`, `wifi_pass`, `client_id`, and `refresh_tok`
in the `bop` namespace. The firmware names them in
`firmware/main/credentials.c`, and the host tools name them in
`tools/device.py` as `CREDENTIAL_KEYS`. A change to one list needs the same
change in the other, or the host safety checks stop finding credentials.

The board holds no Spotify client secret. Provisioning uses PKCE on the host,
and the board exchanges the refresh token for short-lived access tokens. It
stores a rotated refresh token when Spotify returns one.

Firmware conventions for secret data:

- Clear token buffers with `mbedtls_platform_zeroize`, and free them with
  `secure_free` or `secure_free_string` in `spotify/spotify.c`.
- Free a parsed token response with `delete_token_response`, never with a bare
  `cJSON_Delete`.
- Log the fact, never the value. No token, password, or client ID reaches a log
  line.

Host tools obey the same rule. They print credential key names, which are
public constants, and never key values.

## Safety gates in the host tools

These gates are the design, not extra caution. Do not weaken one to make a task
easier.

- `backup_flash.py` reads the 24 KB NVS region first and refuses a full-flash
  backup of a provisioned device. No flag turns the refusal off.
- `flash` and `provision` depend on `backup` in `mise.toml`.
- `restore.py` checks size, digest, and a credential-free NVS region, then
  re-checks the file digest and the device MAC address inside
  `WRITE_VERIFIED_FLASH_COMMAND` after the user approves. Nothing between the
  approval and the write is trusted.
- `deprovision.py` needs typed approvals, then erases NVS and reads it back.
- `leak_scan.py` scans the tracked files and the whole history, and writes its
  findings to `reports/secrets`. Run it before you publish anything.

The esptool helpers run as inline Python source strings in `tools/device.py`
(`PROBE_NVS_COMMAND`, `READ_IDENTITY_COMMAND`, `WRITE_VERIFIED_FLASH_COMMAND`).
They run under the esptool interpreter, which the mise pipx install owns, and
not under the project interpreter. The long comments above each string record
esptool behavior that the code depends on. Read them before you edit a string.

## Conventions

C code uses full words for identifiers, `static` for everything that is not in a
header, `esp_err_t` returns with an `error` variable, and a `bop_` prefix on
public functions. Comments explain why, not what.

Python code uses `from __future__ import annotations`, the standard library
only, `RuntimeError` messages that name the fix, and exit code 130 on
`KeyboardInterrupt`. Ruff owns the format and the lint rules, from
`pyproject.toml`. Run `mise run format` before you send a change.

Some host checks assert on the text of other sources.
`test_display_recovery.py` reads `app_main.c`. `test_playback_feedback.py`
reads `ui.c` and `spotify.c`. `test_deprovision.py` reads `app_main.c`,
`firmware/CMakeLists.txt`, and `firmware/partitions.csv`. `test_provision.py`
reads `credentials.c`. `test_ci.py` reads `.github/workflows/ci.yml` and
`tools/setup_idf.py`. An edit to those files can turn the checks red even when
the code is correct. Read the failing assertion, then decide whether the check
or the code is wrong.

Every new file needs an SPDX header, or an entry in `REUSE.toml` when a header
is impossible. `mise run licenses` is the check.
