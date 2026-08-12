# spot

`spot` is a Spotify remote for the Waveshare ESP32-S3 Touch AMOLED 1.8 board.

It shows the current track and sends touch controls to Spotify. It does not play audio.

<!-- Photo placeholder: Add a photo of the assembled board here. -->

## Quick start

1. Connect the board with a USB data cable.
2. Run `mise run setup`.
3. Run `mise run backup`, **before** you provision.
4. Run `mise run provision`.
5. Run `mise run flash`.

Read [INSTALL.md](INSTALL.md) and [docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md) before you provision the board.

## Install the toolchain

1. Install [mise](https://mise.jdx.dev/).
2. Connect the board with a USB data cable.
3. Run `mise install`.

The command installs Python, esptool, ESP-IDF, and the ESP32-S3 compiler tools.

## Back up the factory firmware

Run `mise run backup` **before** you provision. Order is important here.

This task reads all 16 MB of flash into `backups/factory.bin`. It also writes a SHA-256 file.

The task does not replace an existing backup.

The task reads the credential partition first, and it refuses a full-flash
backup of a provisioned device. That backup would hold your WiFi password and
your Spotify refresh token as plaintext. No flag turns the refusal off.

Run `mise run restore` to write a verified, credential-free image back to the
device. Read [INSTALL.md](INSTALL.md) for both procedures.

## Provision WiFi and Spotify

A Spotify Premium account is necessary for playback controls.

1. Connect the board with USB.
2. Run `mise run provision`.
3. Follow the Spotify dashboard instructions in the terminal.
4. Enter the WiFi values and the Spotify Client ID.
5. Approve the requested Spotify permissions in the browser.

The task uses PKCE. It does not use or store a Spotify client secret.

The task writes the WiFi values, Spotify Client ID, and refresh token directly to the NVS partition. It removes temporary host files after flashing.

Run the task again to replace the saved network or Spotify account.

## Remove Spotify access and device data

1. Connect the board with a USB data cable.
2. Run `mise run deprovision`.
3. Remove Bop access on the Spotify page that opens.
4. Type `REMOVED` when the command asks.
5. Check the shown USB port and MAC address.
6. Type `ERASE`, followed by the shown MAC address.

The command checks the Bop firmware and partition layout. Then it erases the Bop credential partition through USB. It reads the partition after the erase. Then it restarts the provisioning screen.

Read [INSTALL.md](INSTALL.md) and [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) for the full procedure.

## Build, flash, and monitor

1. Run `mise run build`.
2. Run `mise run flash`.
3. Run `mise run monitor`.

The flash task needs a factory backup. On a provisioned board with no backup it
stops, because it cannot take one safely. Read [INSTALL.md](INSTALL.md).

The firmware shows a green `spot` label.

Set `SPOT_PORT` if more than one Espressif board is connected.

## Soak test

1. Run `mise run build-perf`.
2. Run `mise run flash-perf`.
3. Run `mise run monitor-perf`.
4. Play music for 30 minutes.
5. Turn off the WiFi access point during the test.
6. Turn on the access point again.

The performance build shows the LVGL FPS monitor. The serial log records free heap, minimum free heap, WiFi connections, and WiFi reconnects each minute.

## Recover the board

If automatic reset fails, disconnect the USB cable.

1. Press and hold the BOOT button.
2. Connect the USB cable.
3. Release the BOOT button.
4. Run `mise run flash` again.

## Other tasks

- `mise run setup` installs or repairs the pinned ESP-IDF toolchain.
- `mise run restore` writes the verified, credential-free factory image back to the device.
- `mise run clean` removes the firmware build output.
- `mise run monitor-perf` opens the USB serial monitor for the performance firmware.
- `mise run menuconfig` opens the ESP-IDF configuration menu.
- `mise run licenses` checks that every source file records a license and a
  copyright holder.

## Security

`mise run provision` writes your WiFi name and password, your Spotify client ID,
and your Spotify refresh token to the board as plaintext. A person who has the
board can read all four.

Read [SECURITY.md](SECURITY.md) before you provision. It explains the risk, and
it tells you how to report a security problem in private.

## License

This project is licensed under the Apache License 2.0. See [LICENSE](LICENSE).

The Montserrat font stays under the SIL Open Font License. Every downloaded
component keeps its own license.
[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md) lists them all.

## Trademarks

This is an independent project. It is not made by, endorsed by, or connected to
Spotify AB. It makes no claim that Spotify approved it.

Spotify is a trademark of Spotify AB. Espressif and ESP32 are trademarks of
Espressif Systems. Waveshare is a trademark of Waveshare Electronics. This
project uses these names only to say which hardware and services it works
with.
