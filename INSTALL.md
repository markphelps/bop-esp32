# Install Bop

Bop is source software for personal experimentation. It does not include a Spotify client ID or a firmware binary.

The hardware installation process is tested on macOS. The host tools support Windows, but the Windows hardware process is not tested.

## Install the tools

1. Install [mise](https://mise.jdx.dev/).
2. Connect the board with a USB data cable.
3. Run `mise install`.
4. Run `mise run backup` before you provision the board.
5. Run `mise run provision` to authorize Spotify and save the device credentials.
6. Run `mise run flash` to flash the firmware.

Read [PRIVACY.md](PRIVACY.md) and [EULA.md](EULA.md) before you authorize Spotify.

## Back up the factory image

Run `mise run backup` **before** you provision. Order is important here.

`mise run backup` reads all 16 MB of flash into `backups/factory.bin` and writes
a SHA-256 file next to it. `.gitignore` excludes `backups/`.

The command first reads the 24 KB credential partition alone. If that partition
holds `wifi_ssid`, `wifi_pass`, `client_id`, or `refresh_tok`, the command stops
and reads nothing more. A backup taken after provisioning would hold your WiFi
password and your Spotify refresh token as plaintext, so Bop does not take one.
There is no flag that turns this refusal off.

The command applies the same scan to a `backups/factory.bin` that already
exists, before it hashes or accepts the file. It refuses a file that holds any
credential key, even when the size and the SHA-256 file match.

If a valid backup already exists, the command keeps it and reads nothing from
the device. That is why `mise run flash` still works after you provision.

If you have already provisioned and you have no backup, run
`mise run deprovision` first. That gives you an unprovisioned device again.

**A backup you take after `mise run deprovision` is not the factory image.** It
is the Bop flash with an erased credential partition. It is credential-free, and
`mise run restore` accepts it, but it does not return the board to its shipped
state. Only a backup taken before your first flash does that.

## Restore the factory image

`mise run restore` writes `backups/factory.bin` back to the device.

1. If the device still runs Bop firmware and holds credentials, run
   `mise run deprovision` first, so that Spotify stops trusting it. That command
   refuses a board that is not running Bop, so skip this step on a board you
   have already restored.
2. Connect the board with a USB data cable.
3. Run `mise run restore`.
4. Check the image path, USB port, and MAC address that the command shows.
5. Type `RESTORE`, followed by the shown MAC address.

The command restores only an image that is a complete 16 MB, matches its
SHA-256 file, and holds no credential key. It refuses every other image.

A restore overwrites all 16 MB. It replaces the firmware and every value the
device holds, and you cannot undo it.

The command checks the image and the device again after you approve, and it
writes on the same connection. An image or a board that changes while the prompt
waits stops the restore.

## Rehearse the installation from a clean clone

This section is for the maintainer. Do this to prove that the public
instructions work with no hidden local state.

1. Be ready to lose everything on the device. This procedure erases it.
2. Run `mise run deprovision` in the development checkout.
3. Run `mise run restore` in the development checkout. The device now holds the
   image from `backups/factory.bin` again. When that image is the one you took
   before your first flash, the board is back to its shipped state.
4. **Only now**, make the clean clone in a new directory.
5. Copy nothing into the clean clone. No `backups/` directory, no build output,
   no credential, and no local configuration file.
6. In the clean clone, run `mise install`, `mise run backup`,
   `mise run provision`, and `mise run flash`.

Step 3 must finish before step 4. The clean clone takes its own backup at step
6, and that backup is only safe because the device is unprovisioned by then.

## Remove Spotify access and device data

1. Connect the board with a USB data cable.
2. Run `mise run deprovision`.
3. Remove Bop access on the Spotify connected-apps page that opens.
4. Type `REMOVED` after you remove the access.
5. Check the USB port and MAC address that the command shows.
6. Type `ERASE`, followed by the shown MAC address, to approve the device erase.

The command first checks the Bop firmware and partition layout. Then it erases the complete Bop NVS credential partition through USB. It reads the partition after the erase. It stops if a credential key or data remains.

The command restarts the board after a successful read-back. The existing provisioning screen appears. The firmware does not make Spotify requests without credentials.

Do not give or sell the board before this process finishes.

## Authorize again

If you remove Bop access, run `mise run provision` to authorize Bop again. Then run `mise run flash` if the firmware is not installed.

Read [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) when a command stops.
