# How Bop works

## Hardware and USB boot

Bop uses the Waveshare ESP32-S3 Touch AMOLED 1.8 board. The board has an ESP32-S3 processor, 16 MB flash, 8 MB PSRAM, and a 368 by 448 AMOLED display.

The display uses QSPI. The touch controller, AXP2101 power manager, and other low-speed devices use I2C.

The board uses native USB. It appears as an Espressif USB serial device when you connect a USB data cable.

`mise run flash` uses ESP-IDF and esptool to write firmware through USB. The board resets after the flash and starts Bop after a normal power cycle.

If automatic reset fails, hold BOOT while you connect USB. Then run `mise run flash` again.

## ESP-IDF, BSP, and LVGL

ESP-IDF builds the firmware and starts FreeRTOS tasks. `mise install` installs the pinned ESP-IDF 5.5.5 toolchain for ESP32-S3.

The Waveshare BSP configures the panel, touch controller, display brightness, and shared I2C bus.

LVGL draws the interface. `esp_lvgl_port` runs the LVGL task on core 1. Code outside that task holds the display lock before it calls LVGL.

## Startup and task lifecycle

`app_main` starts the display, touch, brightness, and AXP2101 monitor. Display startup also starts the LVGL task on core 1. It then reads the saved credentials from NVS.

If no credentials exist, the display shows `run: mise run provision`. The firmware does not start the Bop UI, album-art, WiFi, or Spotify tasks.

If credentials exist, the firmware initializes the Bop UI. This creates its 250 ms timer on the existing LVGL task and starts the album-art task on core 0.

The firmware starts WiFi connection and time synchronization on core 0. It starts Spotify polling on core 0 after WiFi and time synchronization succeed.

The UI reads a protected playback-state snapshot and redraws only when it changes.

```text
app_main
  |
  +-- display startup and LVGL task on core 1
  +-- touch, brightness, and AXP2101 monitor
  +-- credentials from NVS
  +-- Bop UI timer on core 1 and album-art task on core 0
  +-- WiFi connection and time synchronization on core 0
  +-- Spotify polling task on core 0
```

## Spotify OAuth and NVS

`mise run provision` runs on the host computer. It requires `I AGREE` for [EULA.md](../EULA.md) and [PRIVACY.md](../PRIVACY.md) before it opens Spotify authorization.

The tool uses the Spotify Authorization Code flow with PKCE. The browser returns to a temporary loopback server at `127.0.0.1`.

The provisioning tool writes the WiFi network name, WiFi password, Spotify Client ID, and refresh token to Bop NVS through USB. The tool removes its temporary host files after it writes the NVS image.

The board has no Spotify client secret. On startup, it exchanges the refresh token for a short-lived access token.

The board stores a rotated refresh token when Spotify returns one. NVS stores credentials as plaintext. Read [SECURITY.md](../SECURITY.md) before you provision the board.

## Playback and album art

The Spotify task requests `GET /v1/me/player/currently-playing` every two seconds. It uses the ESP-IDF certificate bundle for HTTPS validation.

A track change updates the protected playback snapshot. The UI shows the title, artist, progress, and album art.

The art task downloads JPEG data into PSRAM. `esp_jpeg` decodes the image to RGB565 pixels.

The task scales album art to 368 by 368 pixels without cropping. It averages the pixels and darkens the result for the background gradient.

After successful decoding, the art task publishes the image. The UI timer validates and owns the current image in memory. It then marks the track as cached.

The task retries a failed download or decode after 5 seconds. It does not download art again after the UI accepts an image for the current track.

## Touch controls and display care

The UI records the touch position when a press starts. It compares the horizontal and vertical distance when the press ends.

A left swipe sends the previous-track command. A right swipe sends the next-track command. A small movement sends pause or play.

The UI gives immediate feedback after an accepted command. The next Spotify response corrects the displayed play state when necessary.

The UI shows `offline` when WiFi is unavailable. The WiFi event handler reconnects when the access point returns. The Spotify task resumes polling after the connection returns.

The AXP2101 monitor detects battery power. After 30 seconds without touch input, battery power changes display brightness to 10 percent.

A touch restores 85 percent brightness. USB power does not activate the idle-dimming rule.

## mise tasks

Run `mise install` first. It installs the pinned host tools and then runs `mise run setup`.

| Command | Function |
| --- | --- |
| `mise run setup` | Installs or repairs the pinned ESP-IDF toolchain. |
| `mise run build` | Builds normal firmware in `firmware/build`. |
| `mise run build-perf` | Builds performance firmware in `firmware/build-perf`. |
| `mise run backup` | Backs up a credential-free 16 MB image. |
| `mise run restore` | Writes a checked credential-free image after explicit approval. |
| `mise run provision` | Authorizes Spotify and writes WiFi and Spotify values to NVS. |
| `mise run deprovision` | Opens Spotify access removal, then erases Bop credentials through USB. |
| `mise run flash` | Flashes normal firmware after a credential-free backup. |
| `mise run flash-perf` | Flashes performance firmware after a credential-free backup. |
| `mise run monitor` | Opens the normal-firmware serial monitor. |
| `mise run monitor-perf` | Opens the performance-firmware serial monitor. |
| `mise run test-host` | Runs host-side checks without a board. |
| `mise run format` | Formats the host Python files with Ruff. |
| `mise run format-check` | Checks host Python formatting without changes. |
| `mise run lint` | Lints the host Python files with Ruff. |
| `mise run secrets` | Scans tracked files, branches, and tags for secrets. |
| `mise run licenses` | Checks license and copyright information. |
| `mise run clean` | Removes ESP-IDF build output. |
| `mise run menuconfig` | Opens the ESP-IDF configuration menu. |

A backup made before the first flash protects the factory image. A backup made after deprovisioning can contain Bop firmware. Do not run a full backup after provisioning.

Read [INSTALL.md](../INSTALL.md) for the supported installation and recovery procedures. Read [docs/TROUBLESHOOTING.md](TROUBLESHOOTING.md) when a command stops.
