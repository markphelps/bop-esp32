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

## The board shows `offline`

The board could not connect to the saved WiFi network. It reconnects when the
network becomes available.

1. Make sure that the WiFi network is available.
2. Make sure that the saved network name and password are correct.
3. If the values are wrong, run `mise run provision` to write new values.

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

A paused player can still appear in Bop after Spotify returns playback state.
If no playback appears, make sure that WiFi and authorization work. Then read
the serial log.

## Spotify rate limiting delays playback updates

Spotify can return a rate limit. Bop waits for the delay that Spotify sends,
or one second when Spotify sends no delay.

Wait for Bop to resume polling. Do not send more touch commands during the
wait. Read the serial log if rate limits happen often.

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

1. If the board has credentials, run `mise run deprovision` first.
2. Connect the board with a USB data cable.
3. Run `mise run restore`.
4. Type `RESTORE`, followed by the shown MAC address, exactly.

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
