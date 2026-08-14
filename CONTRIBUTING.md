# Contributing to Bop

Bop is source-only software for personal experimentation. It has no firmware
binary, package, GitHub Release, release tag, or shared Spotify Client ID.
Read the [source-only notice](README.md#project-status) before you contribute.

## Branch workflow

1. Update your local `main` branch.
2. Create a branch from the current `main` branch.
3. Make one focused change on that branch.
4. Run the local checks that apply to the change.
5. Open a pull request to `main`.
6. For a code change, merge only after the required GitHub checks pass.
7. For a Markdown-only change, record the local checks and merge after review. The GitHub workflow does not start.

Do not commit credentials, WiFi passwords, refresh tokens, flash images, or
contents of `backups/`. Do not add a firmware binary, package, release tag, or
shared Spotify Client ID.

## Local checks

Run `mise install` before you run the local checks. It installs the pinned
host tools and the ESP-IDF toolchain.

Run these checks before you open a pull request:

```text
mise run build
mise run test-host
mise run format
mise run format-check
mise run lint
mise run secrets
mise run licenses
```

`mise run format` can change host Python files. Run `mise run format-check`
after it to make sure that the files are formatted.

`mise run build` builds firmware and needs the ESP-IDF toolchain. The other
commands do not need a board. `mise run secrets` scans tracked files and all
branches and tags. `mise run licenses` checks SPDX license and copyright data.

Do not run `mise run backup`, `mise run restore`, `mise run provision`,
`mise run deprovision`, `mise run flash`, or `mise run flash-perf` as a
documentation or software check. These commands can read or write a physical
device.

Read [docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md) for the complete `mise` task
list and [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) when a command
stops.

## Required GitHub checks

A pull request that changes a non-Markdown path runs these required checks on
Linux:

- `firmware`
- `host-tools (linux)`
- `python-style`
- `secrets`
- `licenses`

A change that contains only `**/*.md` files does not start the workflow. Run
the applicable local documentation checks before you open that pull request.

The workflow is [`.github/workflows/ci.yml`](.github/workflows/ci.yml). The
firmware check builds in the official ESP-IDF container. The other checks run
the matching local `mise` tasks.

## Hardware expectations

Use a Waveshare ESP32-S3 Touch AMOLED 1.8 board for changes that affect device
behavior. This includes firmware, provisioning, flashing, display, touch, and
recovery changes.

macOS is the hardware-tested installation host. Linux builds firmware in CI.
Windows host tools are portable, but Windows hardware installation is untested.
CI runs on Linux only.

If you cannot do a hardware test, state that fact in the pull request. State
the board, host operating system, and command results when you do test
hardware.

## License rules

Project code uses Apache License 2.0. New source files need an SPDX copyright
and license header.

Some documents and generated files use explicit entries in
[REUSE.toml](REUSE.toml) instead of a header. Add a new document to that file
only when a header would be noise for the reader.

Do not change a third-party license or copyright notice. Read
[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md) before you update a bundled
or downloaded dependency. Run `mise run licenses` after every license change.

## Pull request requirements

A pull request must include these items:

- A short description of the change and its purpose.
- The local checks that you ran and their results.
- Hardware-test results, or a statement that hardware was not available.
- Documentation updates when the user procedure, task list, or support level changes.
- No credentials, backups, generated device data, or unsupported release files.

Use clear, factual language. Keep the pull request focused. Respond to review
findings before you merge.

Report a security problem through the private process in [SECURITY.md](SECURITY.md).
Do not put a security problem or private values in a public issue or pull
request.

## Related documents

- [README.md](README.md) has the source-only notice, project status, and support levels.
- [INSTALL.md](INSTALL.md) has the supported installation and recovery procedures.
- [docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md) explains Bop and every `mise` task.
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) has recovery steps.
- [SECURITY.md](SECURITY.md) explains the security policy and credential risk.
- [PRIVACY.md](PRIVACY.md) explains data use and deletion.
- [EULA.md](EULA.md) gives the terms for Bop use.
- [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md) lists included licenses.
