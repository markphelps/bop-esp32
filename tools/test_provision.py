#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mark Phelps
# SPDX-License-Identifier: Apache-2.0

"""Check Spotify-only USB provisioning safeguards."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
import zlib

import provision

ROOT = Path(__file__).resolve().parent.parent


def firmware_source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_protocol_constants_match_the_firmware() -> None:
    source = firmware_source("firmware/main/screenshot.h")
    assert "#define BOP_USB_PROTOCOL_VERSION 1U" in source
    assert "#define BOP_USB_HEADER_SIZE 16U" in source
    assert provision.USB_HEADER_LENGTH == 16
    assert provision.USB_MAX_PAYLOAD == 1093
    assert provision.USB_MAGIC == b"P0PU"
    assert not set(provision.USB_MAGIC) & set(b"nNbBtTsS")
    screenshot = firmware_source("firmware/main/screenshot.c")
    assert "#define BOP_SCREENSHOT_TASK_STACK_SIZE 8192U" in screenshot
    assert 'memcpy(header, "P0PU", 4)' in screenshot
    assert 'memcmp(frame, "P0PU", 4)' in screenshot
    host = firmware_source("tools/provision.py")
    assert "if len(buffer) > USB_NOISE_LIMIT:" in host


def test_provision_does_not_build_or_flash_an_nvs_image() -> None:
    source = firmware_source("tools/provision.py")
    for forbidden in ("write_nvs_csv", "generate_nvs", "flash_nvs", "wifi_ssid", "wifi_pass"):
        assert forbidden not in source


def test_firmware_store_path_does_not_touch_wifi_keys() -> None:
    source = firmware_source("firmware/main/credentials.c")
    start = source.index("bop_credentials_store_spotify")
    end = source.index("bop_credentials_load_state", start)
    store = source[start:end]
    assert 'nvs_set_str(handle, "client_id"' in store
    assert 'nvs_set_str(handle, "refresh_tok"' in store
    assert "nvs_commit(handle)" in store
    assert "wifi_ssid" not in store
    assert "wifi_pass" not in store


def test_complete_wifi_recovery_requires_deprovision() -> None:
    troubleshooting = firmware_source("docs/TROUBLESHOOTING.md")
    assert "run `mise run deprovision`, then use the captive portal" in troubleshooting
    assert "run `mise run provision` to write new values" not in troubleshooting


def test_store_payload_contains_only_spotify_values() -> None:
    class FakeConnection:
        def __init__(self) -> None:
            self.response = b""
            self.request = b""

        def reset_input_buffer(self) -> None:
            pass

        def write(self, data: bytes | bytearray) -> int:
            self.request = bytes(data)
            assert self.request[:1] == provision.USB_START_BYTE
            _, _, command, _, payload_length, _ = provision.USB_HEADER.unpack(
                self.request[1 : 1 + provision.USB_HEADER_LENGTH]
            )
            assert command in {
                provision.USB_QUERY_COMMAND,
                provision.USB_STORE_SPOTIFY_COMMAND,
            }
            if command == provision.USB_QUERY_COMMAND:
                payload = bytes([provision.USB_STATE_WIFI_ONLY])
            else:
                payload = b""
            self.response = (
                provision.USB_HEADER.pack(
                    provision.USB_MAGIC,
                    provision.USB_PROTOCOL_VERSION,
                    provision.USB_STATUS_OK,
                    provision.USB_HEADER_LENGTH,
                    len(payload),
                    zlib.crc32(payload) & 0xFFFFFFFF,
                )
                + payload
            )
            if command == provision.USB_STORE_SPOTIFY_COMMAND:
                request_payload = self.request[1 + provision.USB_HEADER_LENGTH :]
                assert b"wifi_ssid" not in request_payload
                assert b"wifi_pass" not in request_payload
                assert payload_length == len(request_payload)
            return len(data)

        def flush(self) -> None:
            pass

        def read(self, size: int) -> bytes:
            result, self.response = self.response[:size], self.response[size:]
            return result

        def close(self) -> None:
            pass

    connection = FakeConnection()
    with patch.object(provision, "open_connection", return_value=connection):
        provision.store_spotify(["provision.py"], "client-id", "refresh-token")
    assert connection.request


def test_missing_serial_reexecutes_this_script() -> None:
    calls: list[tuple[str, list[str], dict[str, str]]] = []

    def reexec(path: str, arguments: list[str], environment: dict[str, str]) -> None:
        calls.append((path, arguments, environment))
        raise RuntimeError("re-executed")

    with (
        patch.object(provision.importlib.util, "find_spec", return_value=None),
        patch.object(
            provision.screenshot_serial.detect_port,
            "esptool_python",
            return_value=Path("/esptool-python"),
        ),
        patch.object(provision.os, "execve", side_effect=reexec),
    ):
        try:
            provision.provisioning_serial_factory(["tools/provision.py", "--argument"])
        except RuntimeError as error:
            assert str(error) == "re-executed"
        else:
            raise AssertionError("serial fallback did not re-execute provisioning")

    assert len(calls) == 1
    path, arguments, environment = calls[0]
    assert path == "/esptool-python"
    assert arguments == [
        "/esptool-python",
        str((ROOT / "tools/provision.py").resolve()),
        "--argument",
    ]
    assert environment["BOP_PROVISION_PYSERIAL_REEXEC"] == "1"


def test_missing_wifi_stops_before_consent() -> None:
    class QueryConnection:
        def close(self) -> None:
            pass

    with (
        patch("builtins.input") as consent,
        patch.object(provision, "open_connection", return_value=QueryConnection()),
        patch.object(provision, "query_state", return_value=provision.USB_STATE_NONE),
        patch.object(provision, "authorize") as authorize,
    ):
        assert provision.main() == 1
    consent.assert_not_called()
    authorize.assert_not_called()


def test_success_flow_queries_before_authorization() -> None:
    events: list[str] = []

    class QueryConnection:
        def reset_input_buffer(self) -> None:
            pass

        def close(self) -> None:
            events.append("close")

    with (
        patch.object(
            provision,
            "require_legal_agreement",
            side_effect=lambda: events.append("consent"),
        ),
        patch.object(provision, "open_connection", return_value=QueryConnection()),
        patch.object(
            provision,
            "query_state",
            side_effect=lambda _connection: events.append("query") or provision.USB_STATE_WIFI_ONLY,
        ),
        patch.object(provision, "print_dashboard_steps"),
        patch.object(provision, "prompt_value", return_value="client-id"),
        patch.object(
            provision,
            "authorize",
            side_effect=lambda *_args: events.append("authorize") or "refresh-token",
        ),
        patch.object(provision, "store_spotify", side_effect=lambda *_args: events.append("store")),
    ):
        assert provision.main() == 0
    assert events.index("query") < events.index("consent")
    assert events.count("consent") == 1
    assert events.index("consent") < events.index("authorize")
    assert events.index("authorize") < events.index("store")


def main() -> int:
    test_protocol_constants_match_the_firmware()
    test_provision_does_not_build_or_flash_an_nvs_image()
    test_firmware_store_path_does_not_touch_wifi_keys()
    test_complete_wifi_recovery_requires_deprovision()
    test_store_payload_contains_only_spotify_values()
    test_missing_serial_reexecutes_this_script()
    test_missing_wifi_stops_before_consent()
    test_success_flow_queries_before_authorization()
    print("Spotify-only provisioning checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
