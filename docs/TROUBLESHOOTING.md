# Troubleshooting

This guide covers common Bop problems. Read [INSTALL.md](../INSTALL.md) for the
supported installation and factory-restore procedures.

Bop is source-only software for personal experimentation. Read the
[source-only notice](../README.md#project-status) before you use it.

## The USB command cannot find the board

1. Connect the board with a USB data cable.
2. Run `mise install` if the host tools are not installed.
3. Disconnect other Espressif boards.
4. Run the command again.

Bop finds one connected Espressif USB serial port. If more than one board is
connected, set `BOP_PORT` to the port for this board. Do not set it to a
network address.

If the command still cannot find the board, try another USB data cable or USB
port. A charge-only cable cannot transfer data.

## The board does not enter download mode

1. Disconnect the USB cable.
2. Press and hold the BOOT button.
3. Connect the USB cable.
4. Release the BOOT button.
5. Run the USB command again.

Use these steps when automatic reset does not work. Read
[INSTALL.md](../INSTALL.md) before you run `mise run flash`.

## The captive portal does not open

1. Scan the QR code on the Bop screen.
2. Join the **Bop setup AP** that the screen shows.
3. If the page does not open, go to <http://192.168.4.1/>.
4. When the phone reports that the AP has no internet, keep the phone connected.

The Bop setup AP uses a new password after each restart. Use the current screen values.

## The captive portal reports a connection failure

The captive portal stays available after a failed join. It also clears the password field.

1. Select the network again.
2. Enter the WiFi password again.
3. Submit the form.

For a hidden network, select manual entry and enter the network name. Bop saves the values only after it receives an IP address.

## The captive portal marks a network as unsupported

The captive portal supports open, WPA2-Personal, and WPA3-Personal networks. It marks WEP and enterprise networks as unsupported.

Use a supported guest network or change the access-point security mode. Do not enter enterprise credentials through manual entry.

## A saved network is unavailable before USB provisioning

In the **WiFi-only state**, Bop tries the saved network for 15 seconds. It starts the captive portal when the attempt times out.

Restore the saved network, or use the captive portal to select a different network. Then run the USB provisioning command, `mise run provision`.

The USB provisioning command asks only for the Spotify Client ID. It keeps the WiFi values that the captive portal stored.

## The board shows `offline`

The board could not connect to the saved WiFi network. It reconnects when the
network becomes available.

1. Make sure that the WiFi network is available.
2. Make sure that the saved network name and password are correct.
3. If the values are wrong, run `mise run deprovision`, then use the captive portal to save new values and run `mise run provision` again.

`mise run provision` requires the legal agreement and a credential-free backup.
Read [INSTALL.md](../INSTALL.md) before you provision again.

## Time synchronization does not finish

The board synchronizes time with `pool.ntp.org` after it connects to WiFi. It
waits for up to ten two-second attempts before startup stops.

1. Make sure that the WiFi connection works.
2. Make sure that the network can reach `pool.ntp.org`.
3. Restart the board.
4. Read the serial log if the problem continues.

## Spotify authorization does not finish

`mise run provision` needs your own Spotify Client ID. Do not enter a client
secret. Read [EULA.md](../EULA.md) and [PRIVACY.md](../PRIVACY.md) before you
enter `I AGREE`.

Make sure that the Spotify application contains the shown Redirect URI exactly.
By default, it is `http://127.0.0.1:43821/callback`.

If the browser does not open, open the URL that the provisioning command shows.
The command waits five minutes for Spotify to redirect to the host computer.

If port 43821 is unavailable, set `BOP_OAUTH_PORT` to an unused port. Add the
matching `http://127.0.0.1:<port>/callback` URI to the Spotify application.
Then run `mise run provision` again.

## The display says to start Spotify

Bop has no active Spotify player to control. Start Spotify playback on any
device that uses the authorized account. Then wait for Bop to poll Spotify.

Bop polls every 10 seconds while playback is active. It polls every 60 seconds
while playback is paused or idle. A newly active player can take up to 60
seconds to appear.

A paused player can still appear in Bop after Spotify returns playback state.
If no playback appears, make sure that WiFi and authorization work. Then read
the serial log.

## Spotify rate limiting delays playback updates

Spotify can return a rate limit. Bop waits for a positive numeric delay from
Spotify. It waits for 60 seconds when Spotify sends no valid delay.

Bop stops polling during the cooldown. It rejects new normal playback
commands. An affected tap shows a warning. Bop keeps only the newest volume
target and retries it after the cooldown.

Wait for Bop to resume polling. More input does not bypass the cooldown. Read
the serial log if rate limits happen often.

## Volume drag does not change the active player

Bop enables volume drag when Spotify returns `device.volume_percent` as an
integer from 0 through 100. Bop ignores `device.supports_volume` and sends the
request when that value is valid.

Drag at least 48 pixels in a mostly vertical direction. The target percentage
must appear on the artwork. If no percentage appears, read the serial log and
confirm that playback state is available.

Spotify can reject the request after Bop shows the target. The serial log then
shows `Volume command failed`. The next playback poll restores the volume that
Spotify reports.

## Album art does not appear

Bop downloads album art only after it receives an active track with an artwork
URL. Make sure that the track title and artist appear first.

1. Make sure that the board is online.
2. Wait at least five seconds for a failed download or decode to retry.
3. Change to another track to request its artwork.
4. Read the serial log if art still does not appear.

The log can show an album-art download, JPEG, decode, or memory error.

## The display is blank or does not start

Disconnect and reconnect USB power. If the problem continues, enter download
mode and run `mise run flash`.

The flash task takes a factory backup first, and the backup command refuses a
provisioned board. If the board holds credentials and `backups/factory.bin`
does not exist, that task stops. Run `mise run flash -- --force` to flash
without a backup.

CAUTION: `mise run flash -- --force` takes no factory backup. If no valid
backup exists, you cannot restore the board to its shipped state after this
flash.

If flashing does not correct the fault, use the factory-restore procedure only
when you have a valid backup. A restore overwrites all 16 MB of flash. Read
[INSTALL.md](../INSTALL.md) before you restore.

## Read the serial log

1. Connect the board with a USB data cable.
2. Run `mise run monitor`.
3. Reproduce the problem.
4. Press Ctrl+C to stop the monitor.

The log can show WiFi connection, time synchronization, Spotify, album-art,
and display messages. Read [SECURITY.md](../SECURITY.md) before you share a
log. Do not share credentials, refresh tokens, WiFi passwords, or flash images.

## Restore the factory image

Use `mise run restore` only with `backups/factory.bin` and its SHA-256 file.
The command refuses an invalid image or an image with Bop credential keys.

1. Remove Bop access from <https://www.spotify.com/account/apps/>.
2. If the Bop firmware starts, run `mise run deprovision` first.
3. Connect the board with a USB data cable.
4. Run `mise run restore`.
5. Type `RESTORE`, followed by the shown MAC address, exactly.

If Bop does not start, do not run `mise run deprovision`. Use the Spotify page to
remove access. Then run `mise run restore` with the valid backup.

CAUTION: A restore overwrites all flash contents. You cannot undo this action.

A backup from before the first Bop flash can restore the shipped board state.
A backup made after deprovisioning can restore Bop with erased credentials.
It cannot restore the shipped firmware.

If a restore stops after writing begins, do not use the board. Run
`mise run restore` again. Read [INSTALL.md](../INSTALL.md) for the complete
factory-restore procedure.

## Related documents

- [README.md](../README.md) has the source-only notice and support levels.
- [INSTALL.md](../INSTALL.md) has installation, removal, and recovery steps.
- [docs/HOW-IT-WORKS.md](HOW-IT-WORKS.md) explains the firmware and `mise` tasks.
- [CONTRIBUTING.md](../CONTRIBUTING.md) explains the change and review process.
- [SECURITY.md](../SECURITY.md) explains credential risks and private reports.
- [PRIVACY.md](../PRIVACY.md) explains data use and deletion.
- [EULA.md](../EULA.md) gives the terms for Bop use.
- [THIRD_PARTY_LICENSES.md](../THIRD_PARTY_LICENSES.md) lists included licenses.
