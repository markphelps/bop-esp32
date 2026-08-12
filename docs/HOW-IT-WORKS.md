# How spot works

## Board and USB flashing

The board has an ESP32-S3 processor and 16 MB of flash. It also has 8 MB of PSRAM and a 368 by 448 AMOLED display.

The display uses QSPI. The touch controller, AXP2101 power manager, and other low-speed devices use I2C.

The board uses native USB. It appears as an Espressif USB serial device when you connect a USB data cable.

`mise run flash` uses `idf.py` and esptool to write the firmware through USB. The board resets after the flash. It starts `spot` after a normal power cycle.

If the automatic reset fails, hold BOOT while you connect USB. Then run `mise run flash` again.

## Software stack and boot sequence

ESP-IDF builds the firmware and starts FreeRTOS tasks. The Waveshare BSP sets up the panel, touch controller, display brightness, and shared I2C bus.

LVGL draws the interface. `esp_lvgl_port` runs the LVGL task on core 1. Each LVGL call holds the display lock.

The Spotify client runs on core 0. It polls Spotify and publishes a protected playback snapshot. The UI reads this snapshot during its timer tick.

```text
app_main
  |
  +-- display, touch, brightness, and AXP2101 monitor
  +-- LVGL UI task on core 1
  +-- WiFi connection and time synchronization on core 0
  +-- Spotify polling task on core 0
  +-- one-minute soak diagnostics task
```

The app first starts the display and sets normal brightness. It then loads the saved credentials from NVS.

If no credentials exist, the screen tells you to run `mise run provision`. If credentials exist, the app connects WiFi, synchronizes time, and starts Spotify polling.

## Spotify authorization and token storage

`mise run provision` runs on the host computer. It opens the Spotify Authorization Code flow with PKCE in your browser.

The browser redirects to a temporary loopback server on `127.0.0.1`. The host exchanges the authorization code for a refresh token.

The provisioning tool writes the WiFi network name, WiFi password, Spotify Client ID, and refresh token to the board NVS partition through USB.

The board has no Spotify client secret. At startup, it uses the refresh token to get a short-lived access token.

The board stores a rotated refresh token when Spotify returns one. It clears temporary host files after provisioning.

`mise run deprovision` opens Spotify's connected-apps page on the host. After you remove Bop access and approve the erase, it erases the complete NVS credential partition through USB. It reads the partition after the erase, then restarts the device. The credential loader treats the erased partition as unprovisioned, so the firmware shows the provisioning screen and does not start Spotify polling.

## Playback, album art, and color

The Spotify task requests `GET /v1/me/player/currently-playing` every two seconds. It uses the ESP-IDF certificate bundle for HTTPS validation.

A track change updates the shared snapshot. The UI shows the title, artist, progress, and a 300 by 300 album image URL.

The art worker downloads the JPEG into PSRAM. `esp_jpeg` decodes the JPEG to RGB565 pixels.

The art worker scales the image to 368 by 368 without cropping. It averages the pixels and darkens the result for the background gradient.

The worker keeps the current track image in memory. It does not download the same image again for an unchanged track.

## Touch controls and display care

The UI stores the touch position when a press starts. On release, it compares the horizontal and vertical distance.

A left swipe sends the previous-track command. A right swipe sends the next-track command. A small movement sends pause or play.

The UI shows an animation for every accepted gesture. It updates play state immediately, then corrects that state with the next Spotify response.

The UI shows a small `offline` label when WiFi is unavailable. The WiFi event handler reconnects when the access point returns. The Spotify task waits for this connection, then continues polling.

The AXP2101 monitor detects battery power. When the board is on battery and you do not touch it for 30 seconds, the display brightness changes to 10 percent.

A touch restores normal 85 percent brightness. USB power does not activate this idle dimming rule.

## Soak diagnostics

Use the performance build for an FPS reading:

1. Run `mise run build-perf`.
2. Run `mise run flash-perf`.
3. Run `mise run monitor-perf`.
4. Play music for 30 minutes.
5. Turn off the WiFi access point during the test.
6. Turn on the access point again.

The performance build sets `LV_USE_PERF_MONITOR`. LVGL shows the FPS monitor at the top left.

The serial log writes a `spot_soak` line every minute. The line includes free heap, minimum free heap, free PSRAM, minimum free PSRAM, WiFi state, connections, disconnects, and reconnect attempts.

Save the monitor output with the test date. Make sure that the log has no unexpected reset, WiFi reconnects after the outage, and an FPS value of 25 or more.

## mise tasks

- `mise run setup` installs or repairs the pinned ESP-IDF toolchain.
- `mise run build` builds the normal firmware in `firmware/build`.
- `mise run build-perf` builds a separate firmware with the LVGL FPS monitor in `firmware/build-perf`.
- `mise run backup` reads the 24 KB credential partition first, refuses a provisioned device, then reads the factory 16 MB flash image and writes its SHA-256 file.
- `mise run restore` writes a size-, digest-, and credential-checked factory image back to the device after explicit approval.
- `mise run provision` gets Spotify authorization and writes WiFi and Spotify values to NVS.
- `mise run deprovision` removes Spotify access and erases the Bop credential partition through USB.
- `mise run flash` writes the normal firmware. It needs a factory backup, and it stops on a provisioned board that has none.
- `mise run flash-perf` writes the performance firmware. It needs a factory backup on the same terms.
- `mise run monitor` opens the USB serial monitor for the normal firmware.
- `mise run monitor-perf` opens the USB serial monitor for the performance firmware. Press Ctrl+C to close either monitor.
- `mise run test-host` runs the host-side checks without a board.
- `mise run clean` removes the normal firmware build output.
- `mise run menuconfig` opens the ESP-IDF configuration menu.
- `mise run licenses` checks that every source file records a license and a copyright holder.
