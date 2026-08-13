#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mark Phelps
# SPDX-License-Identifier: Apache-2.0

"""Verify the warm-reset AMOLED recovery sequence."""

from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent / "firmware/main/app_main.c"


def main() -> int:
    source = SOURCE.read_text(encoding="utf-8")
    start = source.index("static esp_err_t recover_display_panel(void)")
    end = source.index("static lv_display_t *start_display(void)", start)
    recovery = source[start:end]

    operations = (
        "esp_lcd_panel_disp_on_off(display_panel, false)",
        "esp_lcd_panel_reset(display_panel)",
        "esp_lcd_panel_init(display_panel)",
        "esp_lcd_panel_disp_on_off(display_panel, true)",
    )
    positions = [recovery.index(operation) for operation in operations]
    assert positions == sorted(positions)
    assert "recovery_error = recover_display_panel();" in source
    assert source.index("recovery_error = recover_display_panel();") < source.index(
        "lvgl_port_add_disp(&display_configuration)"
    )

    print("Display warm-reset recovery check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
