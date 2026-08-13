#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mark Phelps
# SPDX-License-Identifier: Apache-2.0

"""Verify Ctrl+C behavior for host-side task runners without a board."""

from __future__ import annotations

import configparser
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
from unittest.mock import patch

import backup_flash

ROOT = Path(__file__).resolve().parent.parent
MONITOR_CONFIG = ROOT / "tools" / "esp-idf-monitor.cfg"


def test_monitor_tasks_use_ctrl_c() -> None:
    with (ROOT / "mise.toml").open("rb") as source:
        tasks = tomllib.load(source)["tasks"]

    for name in ("monitor", "monitor-perf"):
        assert (
            tasks[name]["env"]["ESP_IDF_MONITOR_CFGFILE"]
            == "{{config_root}}/tools/esp-idf-monitor.cfg"
        )

    config = configparser.ConfigParser()
    assert config.read(MONITOR_CONFIG) == [str(MONITOR_CONFIG)]
    assert config["esp-idf-monitor"]["exit_key"] == "C"


def test_run_idf_cancels_child() -> None:
    if os.name == "nt":
        print("Skipping POSIX process-group test on Windows.")
        return

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        (root / "export.sh").write_text('export PATH="$FAKE_BIN:$PATH"\n', encoding="utf-8")

        # The fake idf.py installs its trap, then reports that it is ready. The
        # test waits for that file instead of a fixed delay. A delay short
        # enough to keep the test fast is also short enough to lose the race on
        # a loaded machine, and the signal then arrives before the trap.
        ready = root / "ready"
        fake_idf = bin_dir / "idf.py"
        fake_idf.write_text(
            f"#!/bin/sh\ntrap 'exit 130' INT\n: > '{ready}'\nwhile :; do sleep 1; done\n",
            encoding="utf-8",
        )
        fake_idf.chmod(0o755)

        environment = os.environ | {"FAKE_BIN": str(bin_dir), "BOP_IDF_PATH": str(root)}
        process = subprocess.Popen(
            [sys.executable, "tools/run_idf.py", "clean"],
            cwd=ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        deadline = time.monotonic() + 30
        while not ready.exists():
            assert process.poll() is None, "the fake idf.py stopped before it was ready"
            assert time.monotonic() < deadline, "the fake idf.py never reported that it was ready"
            time.sleep(0.02)
        os.killpg(process.pid, signal.SIGINT)
        _, stderr = process.communicate(timeout=10)

        assert process.returncode == 130
        assert "Canceled." in stderr


def test_backup_cleans_up_when_canceled() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        partial = root / "backups" / "factory.bin.partial"
        with (
            patch.object(backup_flash, "project_root", return_value=root),
            patch.object(backup_flash, "esptool_path", return_value="esptool"),
            patch.object(backup_flash, "detect_usb_port", return_value="fake-port"),
            patch.object(backup_flash, "probe_device_credentials", return_value=[]),
            patch.object(backup_flash.subprocess, "run", side_effect=KeyboardInterrupt),
        ):
            try:
                backup_flash.main()
            except KeyboardInterrupt:
                pass
            else:
                raise AssertionError("backup did not pass through Ctrl+C")
        assert not partial.exists()


def main() -> int:
    test_monitor_tasks_use_ctrl_c()
    test_run_idf_cancels_child()
    test_backup_cleans_up_when_canceled()
    print("Host Ctrl+C checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
