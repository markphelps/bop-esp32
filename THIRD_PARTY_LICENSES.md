# Third-party licenses

Bop is licensed under the Apache License 2.0. See [LICENSE](LICENSE).

This page lists the first-level third-party works that Bop includes or
downloads. Each work keeps its own license.

A dependency can contain further third-party works of its own. This page names
only the ones it calls out. To see the full set, read the license files inside
each dependency after `mise run setup` and a build.

Machine-readable license data is in [REUSE.toml](REUSE.toml) and in the SPDX
headers of the source files. Run `mise run licenses` to check it.

## Included in this repository

| Work | Version | License | Files |
| --- | --- | --- | --- |
| [Montserrat](https://github.com/JulietaUla/Montserrat) | — | [OFL-1.1](LICENSES/OFL-1.1.txt) | `firmware/main/ui/fonts/Montserrat-Bold.ttf`, `firmware/main/ui/fonts/lv_font_montserrat_bold_24.c` |

Copyright 2011 The Montserrat Project Authors
(https://github.com/JulietaUla/Montserrat)

`lv_font_montserrat_bold_24.c` is an LVGL bitmap font built from
`Montserrat-Bold.ttf`. It contains font data, so it stays under the SIL Open
Font License.

## ESP-IDF

| Framework | Version | License |
| --- | --- | --- |
| [ESP-IDF](https://github.com/espressif/esp-idf) | 5.5.5 | Apache-2.0 |

`mise run setup` clones ESP-IDF from GitHub at tag `v5.5.5`. It writes it
outside this repository, to `~/.local/share/esp-idf` by default. Set
`BOP_IDF_PATH` to use a different path. The Apache-2.0 grant is the `LICENSE`
file at the root of the clone.

ESP-IDF is Apache-2.0 as a whole, but it links further works under their own
licenses. FreeRTOS, mbedTLS, newlib, picolibc, and wpa_supplicant all reach the
firmware image this way, and the clone carries a license file for each one:

| Work | License file in the clone |
| --- | --- |
| FreeRTOS | `components/freertos/FreeRTOS-Kernel/LICENSE.md` |
| mbedTLS | `components/mbedtls/mbedtls/LICENSE` |
| newlib and picolibc | `components/newlib/COPYING.NEWLIB`, `components/newlib/COPYING.picolibc` |
| wpa_supplicant | `components/wpa_supplicant/COPYING` |

`firmware/main/spotify/spotify.c` and `firmware/main/ui/art.c` also use the
ESP-IDF certificate bundle, so the Mozilla NSS root certificates reach the
image. That bundle is certificate data and not code. It comes from the Mozilla
NSS root store through curl's `mk-ca-bundle.pl`, and it is in
`components/mbedtls/esp_crt_bundle/cacrt_all.pem`. **The clone carries no
license file for it.** Read the header of that PEM file for its source.

The compiler toolchain that ESP-IDF installs also contributes runtime code to
the image. Read the license files in `~/.espressif` for those terms.

## Components downloaded when you build

The build downloads these components from
[components.espressif.com](https://components.espressif.com). They are not in
this repository. `mise run build` writes them to
`firmware/managed_components/`, which `.gitignore` excludes.

`firmware/dependencies.lock` pins each version and content hash.

| Component | Version | License |
| --- | --- | --- |
| espressif/cmake_utilities | 0.5.3 | Apache-2.0 |
| espressif/esp_codec_dev | 1.5.11 | Apache-2.0 |
| espressif/esp_io_expander | 1.2.1 | Apache-2.0 |
| espressif/esp_io_expander_tca9554 | 2.0.3 | Apache-2.0 |
| espressif/esp_jpeg | 1.3.1 | Apache-2.0 |
| espressif/esp_lcd_co5300 | 2.1.0 | Apache-2.0 |
| espressif/esp_lcd_panel_io_additions | 1.0.1~1 | Apache-2.0 |
| espressif/esp_lcd_touch | 1.2.1 | Apache-2.0 |
| espressif/esp_lcd_touch_cst816s | 1.1.1~2 | Apache-2.0 |
| espressif/esp_lcd_touch_ft5x06 | 1.1.0~2 | Apache-2.0 |
| espressif/esp_lvgl_port | 2.9.0 | Apache-2.0 |
| lvgl/lvgl | 9.5.0 | MIT |
| waveshare/esp32_s3_touch_amoled_1_8 | 2.0.3 | Apache-2.0 |

LVGL 9.5.0 is Copyright (c) 2025 LVGL Kft. LVGL is MIT as a whole, but it
bundles further libraries under their own licenses. Its `COPYRIGHTS.md` names
them, and its per-library license files are under `src/`.

`espressif/esp_jpeg` 1.3.1 is Apache-2.0, but it includes TJpgDec R0.03,
Copyright (C) 2021 ChaN. TJpgDec has its own permissive license: it puts no
restriction on use, and it requires that a redistribution of the source keeps
the copyright notice. The notice is in `tjpgd/tjpgd.c` inside the downloaded
component.

Each downloaded component keeps its own license file. The name differs by
component: `license.txt`, `LICENSE`, or `LICENCE.txt`. Read them in
`firmware/managed_components/` after a build.

## Host tools

These tools run on your computer. They are not part of the firmware, and Bop
does not redistribute them.

| Tool | Version | License | Installed by |
| --- | --- | --- | --- |
| Python | 3.13.15 | PSF-2.0 | `mise install` |
| pipx | 1.16.6 | MIT | `mise install` |
| esptool | 5.3.1 | GPL-2.0-or-later | `mise install` |
| CMake | 3.30.9 | BSD-3-Clause | `mise install` |
| Ninja | 1.13.2 | Apache-2.0 | `mise install` |
| REUSE | 6.2.0 | Apache-2.0 AND CC0-1.0 AND CC-BY-SA-4.0 AND GPL-3.0-or-later | `mise run licenses`, on demand |
| charset-normalizer | unpinned | MIT | `mise run licenses`, as a REUSE extra |

`mise.toml` pins each version. The `[tools]` block pins the first five. The
`[tasks.licenses]` task pins REUSE, because it downloads REUSE only when you
run the check. That task also asks for REUSE's `charset-normalizer` extra,
which REUSE 6.2.0 needs to detect file encodings. The extra is not pinned.

## Trademarks

Bop is an independent project. It is not made by, endorsed by, or connected to
Spotify AB.

Spotify is a trademark of Spotify AB. Espressif and ESP32 are trademarks of
Espressif Systems. Waveshare is a trademark of Waveshare Electronics. Every
other product name is a trademark of its owner. Bop uses these names only to
say which hardware and services it works with.

Bop makes no claim that Spotify approved it. Read [SECURITY.md](SECURITY.md)
and [EULA.md](EULA.md) before you use it.

## How this list was checked

Checked on 2026-08-12, for the exact version of each component in
`firmware/dependencies.lock`.

Each component license came from the component's own license file. Read that
file in `firmware/managed_components/` after a build, or download it from this
API:

```
https://components.espressif.com/api/components/<namespace>/<name>
```

That response holds a `versions` array. Find the entry for the pinned version,
then read its `license.url` field to get the license file.

Do not trust the `license.name` field in the same entry. For seven of these
thirteen versions the registry reports `Custom`, but the license file itself is
verbatim Apache-2.0 or verbatim MIT. **The file contents are the authority.**

Each host tool license came from the license that its project publishes. The
esptool and REUSE rows came from the PyPI metadata for the pinned version.

Update this page whenever `firmware/dependencies.lock` changes, or whenever the
`[tools]` block or the `[tasks.licenses]` pin in `mise.toml` changes.
