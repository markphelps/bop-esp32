# Security policy

Bop is source code for personal experimentation. It is not a product, and it
has no support contract. This page tells you what is supported, how to report
a problem in private, and which risks the project accepts.

## Supported versions

| Version | Supported |
| --- | --- |
| Latest commit on `main` | Yes |
| Any older commit | No |

Bop publishes no firmware binary, no package, and no release tag. The latest
commit on `main` is the only supported version. To get a fix, pull `main` and
build again.

## Report a problem in private

Use GitHub private vulnerability reporting:

1. Open <https://github.com/markphelps/bop-esp32>.
2. Select the **Security** tab.
3. Select **Report a vulnerability**.

Do not open a public issue for a security problem. Do not put credentials, a
refresh token, or a flash image in a report.

Include:

- What you did, and what happened.
- The commit SHA you built.
- Your board and your ESP-IDF version.

## What to expect

| Step | Maximum time |
| --- | --- |
| First reply | 7 days |
| Assessment, with a fix plan or a reason to decline | 30 days |

This is a personal project, so these times are a goal and not a guarantee. The
fix lands on `main`, and the advisory says which commit carries it. You and the
maintainer agree when to make the advisory public. This decision is separate for
each report.

## Accepted risk: Bop stores credentials as plaintext

**A person who has your board can read your WiFi password and your Spotify
refresh token.**

`mise run provision` writes the WiFi SSID, the WiFi password, the Spotify
client ID, and the Spotify refresh token to the NVS partition **as plaintext**.
Bop does not enable NVS encryption, Secure Boot, or flash encryption. Anyone
who can attach a USB cable to the board can read the whole flash, and can
recover all four values.

This is a deliberate design decision, not a defect. Flash encryption on the
ESP32-S3 needs eFuse key provisioning. That step can be irreversible, and it
can stop you from restoring the factory image. Bop is source code for personal
experimentation. It uses the simpler design.

Protect yourself this way:

- Keep the board where you keep your other personal hardware.
- If your Spotify account holds private data, use a different account for Bop.
- Consider a guest WiFi network instead of your main one.
- Run `mise run deprovision` before you give the board away, sell it, or throw
  it out. That command opens the Spotify connected-apps page. Remove Bop access
  there. The command then erases the credential partition and checks the erase.
  The command cannot remove the Spotify access for you, so do not skip the
  browser step.

Do not report plaintext NVS storage as a vulnerability. It is documented here
on purpose. A way to read the credentials **without physical access** is a
vulnerability, so report that.

## Other things that are out of scope

These are known and accepted. They are not vulnerabilities:

- A full-flash image holds whatever the flash holds. `mise run backup` reads the
  credential partition first and refuses a provisioned device, so a backup it
  produces holds no Bop credential. It applies the same scan to an image that
  already exists, and `mise run restore` refuses an image that fails the scan.
  Keep the `backups/` directory private anyway. `.gitignore` excludes it.
- The serial monitor prints diagnostic output. Read it before you share it.
- Bop trusts the Spotify Web API over TLS with the certificate bundle that
  ESP-IDF ships. It pins no certificate.
- Bop asks you for your own Spotify client ID. The project ships none, and it
  shares none.
- The provisioning flow uses OAuth PKCE, so it never asks for a Spotify client
  secret and never stores one.

## Related documents

Bop is an independent project. It is not made by, endorsed by, or connected to
Spotify AB. [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md) carries the full
trademark notice.

Read [EULA.md](EULA.md) and [PRIVACY.md](PRIVACY.md) before you provision the
board.
