# Install Bop

Bop is source software for personal experimentation. It includes no firmware binary and no shared Spotify Client ID.

Use this procedure on macOS. Bop hardware installation is tested on macOS. Linux builds the firmware in CI. Windows host tools are portable, but Windows hardware installation is untested.

## Before you start

You need a Waveshare ESP32-S3 Touch AMOLED 1.8 board and a USB data cable.

You also need a WiFi network, a Spotify Premium account, and your own Spotify application. Read [EULA.md](EULA.md), [PRIVACY.md](PRIVACY.md), and [SECURITY.md](SECURITY.md) before you authorize Spotify.

Spotify limits Streaming integrations to Approved Devices. Spotify does not clearly state how this rule applies to a source-only ESP32 repository. Bop does not claim Spotify approval.

## Create a Spotify application

1. Open <https://developer.spotify.com/dashboard>.
2. Select **Create app**.
3. Enter an application name and description.
4. Add `http://127.0.0.1:43821/callback` as a Redirect URI.
5. Select **Web API**.
6. Accept the Spotify terms and save the application.
7. Open **Settings** and copy the Client ID.

Do not create or enter a client secret. Bop uses OAuth PKCE and needs only the Client ID.

## Install and provision

1. Install Git and [mise](https://mise.jdx.dev/).
2. Run `git clone https://github.com/markphelps/bop-esp32.git`.
3. Run `cd bop-esp32`.
4. Connect the board with a USB data cable.
5. Run `mise install`.
6. Run `mise run provision`.
7. Read the shown paths for `EULA.md` and `PRIVACY.md`.
8. Type `I AGREE` exactly when the command asks.
9. Enter the WiFi values and Spotify Client ID.
10. Complete Spotify authorization in the browser.
11. Run `mise run flash`.

`mise run provision` requires a credential-free backup. A backup made before the first flash is the factory image. `mise run flash` also requires a valid backup.

If a valid backup exists, the backup task validates it and does not read the board. If an incomplete or invalid backup exists, the task stops. Move that backup out of `backups/` before you try again.

If no backup exists, the task reads the credential partition first. It reads all flash only when the partition has no credentials.

The backup command reads the 24 KB credential partition first. It refuses to make a full backup after provisioning. This prevents a backup from holding WiFi credentials or a refresh token.

Set `BOP_PORT` when more than one Espressif board is connected. Set `BOP_IDF_PATH` to use an ESP-IDF checkout outside the default location.

After the flash, the board restarts. It connects to WiFi and shows Spotify playback when a player is active.

## Update the firmware

1. Pull the latest source changes.
2. Run `mise install`.
3. Run `mise run flash`.

The existing factory backup lets the flash task run after provisioning. Keep this backup for factory recovery.

Deprovisioning erases credentials. It does not restore factory firmware. A new backup after deprovisioning contains Bop firmware, not the shipped factory image.

## Remove Spotify access and credentials

1. Connect the board with a USB data cable.
2. Run `mise run deprovision`.
3. Remove Bop access on the Spotify page that opens.
4. Type `REMOVED` exactly when the command asks.
5. Read the shown USB port and MAC address.
6. Type `ERASE`, followed by the shown MAC address.

The command erases the Bop credential partition. It reads the partition after the erase. Then it restarts the board in provisioning mode.

## Restore the factory image

`mise run restore` writes `backups/factory.bin` to the device. The image must be 16 MB, match its SHA-256 file, and contain no Bop credential key.

CAUTION: A restore overwrites all flash contents. You cannot undo this operation.

1. If Bop still has credentials, run `mise run deprovision` first.
2. Connect the board with a USB data cable.
3. Run `mise run restore`.
4. Read the image path, USB port, and MAC address.
5. Type `RESTORE`, followed by the shown MAC address.

A backup made before your first flash restores the shipped board state. A backup made after deprovisioning restores Bop with erased credentials. It does not restore the shipped firmware.

## Recover a board that does not reset

1. Disconnect the USB cable.
2. Press and hold the BOOT button.
3. Connect the USB cable.
4. Release the BOOT button.
5. Run `mise run flash`.

Read [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) if a command stops.
