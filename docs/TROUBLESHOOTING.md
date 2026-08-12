# Troubleshooting

## The deprovision command cannot find the board

1. Connect the board with a USB data cable.
2. Disconnect other Espressif boards.
3. Run `mise run deprovision` again.

If more than one board is connected, set `SPOT_PORT` to the correct USB serial port. Do not use a network address.

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
