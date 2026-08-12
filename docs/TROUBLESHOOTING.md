# Troubleshooting

## The deprovision command cannot find the board

1. Connect the board with a USB data cable.
2. Disconnect other Espressif boards.
3. Run `mise run deprovision` again.

If more than one board is connected, set `BOP_PORT` to the correct USB serial port. Do not use a network address.

If the board does not reset automatically, hold BOOT while you connect the USB cable. Then run the command again.

## You did not remove Bop access

The command stops if you do not type `REMOVED`. No device data is erased.

Open <https://www.spotify.com/account/apps/>. Remove Bop access. Then run `mise run deprovision` again.

## You did not approve the erase

The command stops if you do not type `ERASE`, followed by the shown MAC address. No device data is erased.

Check the shown USB port and MAC address. Then enter the complete approval text for that device.

## The device identity check stops

The command only erases a device with the Bop firmware and partition layout. Connect the correct Bop device through USB.

If the device has different firmware, flash Bop before you run the command. Do not use this command with another ESP32-S3 board.

## The erase or read-back stops

Do not give or sell the device. The device can still contain credentials.

Keep the USB cable connected. Run `mise run deprovision` again. The command stops if the read-back still contains data or a credential key.

## You need to authorize Bop again

1. Connect the board with a USB data cable.
2. Run `mise run provision`.
3. Authorize Bop in the Spotify browser page.

The provisioning command writes new WiFi and Spotify credentials to the Bop NVS partition.

## The board was set up before the name change

Bop changed its NVS namespace and its firmware project name. Both values now use the product name. A board set up before that change shows one of two symptoms.

**The board asks for provisioning again.** The firmware reads the credentials from the new namespace. The older provisioning command wrote them under the old name, so the firmware finds none.

1. Connect the board with a USB data cable.
2. Run `mise run provision`.

**The deprovision command does not recognize the firmware.** `mise run deprovision` reads the project name from the firmware image. An older image carries the old name, so the identity check stops.

If a factory backup exists, write the current firmware first:

1. Connect the board with a USB data cable.
2. Run `mise run flash`.
3. Run `mise run deprovision` again.

`mise run flash` depends on the backup task. That task keeps a valid backup and reads nothing from the board.

If no factory backup exists, no command in this checkout will flash, provision, or deprovision that board. `mise run backup` refuses a provisioned board, `mise run flash` needs that backup, and `mise run deprovision` refuses the older firmware. A checkout from before the rename still matches the older firmware:

1. Open <https://www.spotify.com/account/apps/>.
2. Remove Bop access on that page.
3. Check out the commit before the rename, in a second directory.
4. Run `mise run deprovision` there.
5. Return to this checkout.
6. Run `mise run backup`.
7. Run `mise run provision`.
8. Run `mise run flash`.

Step 4 makes the board unprovisioned, so the backup in step 6 succeeds. That step reads all 16 MB from the board.

CAUTION: Do not treat the step 6 backup as the factory image. It is the Bop flash with an erased credential partition. `mise run restore` accepts it, but it does not return the board to its shipped state. Read [INSTALL.md](../INSTALL.md).
