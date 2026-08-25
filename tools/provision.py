#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mark Phelps
# SPDX-License-Identifier: Apache-2.0

"""Authorize Spotify and send only Spotify credentials to the device."""

from __future__ import annotations

import base64
from getpass import getpass
import hashlib
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
import importlib.util
import json
import os
from pathlib import Path
import secrets
import struct
import sys
import time
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen
import webbrowser
import zlib

import screenshot as screenshot_serial

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
SCOPES = "user-read-playback-state user-modify-playback-state"
CALLBACK_HOST = "127.0.0.1"
CALLBACK_PATH = "/callback"
DEFAULT_CALLBACK_PORT = 43821
# These bytes avoid the legacy serial playback keys (`n`, `b`, `t`, and `s`).
# An older Bop ignores the preflight frame instead of changing playback before
# the host reports that firmware support is missing.
USB_MAGIC = b"P0PU"
USB_START_BYTE = b"\x7f"
USB_PROTOCOL_VERSION = 1
USB_HEADER = struct.Struct("<4sBBHII")
USB_HEADER_LENGTH = USB_HEADER.size
USB_MAX_PAYLOAD = 65 + 1024 + 4
USB_QUERY_COMMAND = 1
USB_STORE_SPOTIFY_COMMAND = 2
USB_STATUS_OK = 0
USB_STATUS_NO_CREDENTIALS = 1
USB_STATUS_MALFORMED = 2
USB_STATUS_UNSUPPORTED_VERSION = 3
USB_STATUS_INVALID_LENGTH = 4
USB_STATUS_INTEGRITY = 5
USB_STATUS_STORAGE = 6
USB_STATUS_UNSUPPORTED_COMMAND = 7
USB_STATE_NONE = 0
USB_STATE_WIFI_ONLY = 1
USB_STATE_COMPLETE = 2
USB_NOISE_LIMIT = 65536
USB_RESPONSE_TIMEOUT = 8
open_serial = screenshot_serial.open_serial


def provisioning_serial_factory(arguments: list[str]) -> Any:
    if importlib.util.find_spec("serial") is None:
        python = screenshot_serial.detect_port.esptool_python()
        if python is None or os.environ.get("BOP_PROVISION_PYSERIAL_REEXEC") == "1":
            raise RuntimeError("pyserial is unavailable. Run `mise install` to install esptool.")
        environment = os.environ.copy()
        environment["BOP_PROVISION_PYSERIAL_REEXEC"] = "1"
        os.execve(
            str(python),
            [str(python), str(Path(__file__).resolve()), *arguments[1:]],
            environment,
        )
        raise AssertionError("The esptool Python re-execution returned")
    import serial  # pyright: ignore[reportMissingModuleSource]

    return serial.Serial


serial_factory = provisioning_serial_factory
LEGAL_DOCUMENTS = ("EULA.md", "PRIVACY.md")
LEGAL_AGREEMENT_PROMPT = (
    "Type I AGREE to accept EULA.md and PRIVACY.md before Spotify authorization: "
)


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def print_legal_documents() -> None:
    print("Read these documents before Spotify authorization:")
    for name in LEGAL_DOCUMENTS:
        path = project_root() / name
        if not path.is_file():
            raise RuntimeError(f"Required legal document is unavailable: {name}")
        print(f"- {name}: {path.as_uri()}")


def require_legal_agreement() -> None:
    print_legal_documents()
    if input(LEGAL_AGREEMENT_PROMPT) != "I AGREE":
        raise RuntimeError(
            "Provisioning stopped. Type I AGREE exactly to start Spotify authorization."
        )


def prompt_value(label: str, default: str = "", *, secret: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    value = getpass(f"{label}{suffix}: ") if secret else input(f"{label}{suffix}: ").strip()
    return value or default


def make_sure_value_fits(
    label: str, value: str, maximum_bytes: int, *, allow_empty: bool = False
) -> None:
    length = len(value.encode("utf-8"))
    if (not allow_empty and length == 0) or length > maximum_bytes:
        raise ValueError(f"{label} must contain 1 to {maximum_bytes} UTF-8 bytes")


def print_dashboard_steps(redirect_uri: str) -> None:
    print("\nSpotify app setup:")
    print("1. Open https://developer.spotify.com/dashboard and select Create app.")
    print("2. Enter an app name and description.")
    print(f"3. Add this Redirect URI exactly: {redirect_uri}")
    print("4. Select Web API, accept the terms, and save the app.")
    print("5. Open Settings and copy the Client ID. Do not create a client secret.\n")


class CallbackServer(HTTPServer):
    expected_state: str
    result: dict[str, str] | None = None


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        server = cast(CallbackServer, self.server)
        parsed = urlparse(self.path)
        if parsed.path != CALLBACK_PATH:
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        query = parse_qs(parsed.query)
        state = query.get("state", [""])[0]
        if not secrets.compare_digest(state, server.expected_state):
            server.result = {"error": "OAuth state did not match"}
        elif "error" in query:
            server.result = {"error": query["error"][0]}
        elif "code" in query:
            server.result = {"code": query["code"][0]}
        else:
            server.result = {"error": "Spotify did not return an authorization code"}

        successful = "code" in server.result
        message = (
            "Authorization complete. You can close this page."
            if successful
            else "Authorization failed. Return to the terminal."
        )
        body = f"<!doctype html><meta charset=utf-8><title>bop</title><p>{message}</p>".encode()
        self.send_response(HTTPStatus.OK if successful else HTTPStatus.BAD_REQUEST)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        _ = (format, args)


def authorize(client_id: str, redirect_uri: str, port: int) -> str:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode("ascii")
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    state = secrets.token_urlsafe(32)
    parameters = urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": SCOPES,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
            "state": state,
        }
    )
    authorization_url = f"{AUTH_URL}?{parameters}"

    server = CallbackServer((CALLBACK_HOST, port), CallbackHandler)
    server.expected_state = state
    server.timeout = 1
    print(f"Open this URL if the browser does not open:\n{authorization_url}\n")
    if os.environ.get("BOP_NO_BROWSER") != "1":
        webbrowser.open(authorization_url)

    deadline = time.monotonic() + 300
    while server.result is None and time.monotonic() < deadline:
        server.handle_request()
    server.server_close()
    if server.result is None:
        raise TimeoutError("Spotify did not redirect to this computer within 5 minutes")
    if "error" in server.result:
        raise RuntimeError(f"Spotify authorization failed: {server.result['error']}")

    token = token_request(
        {
            "client_id": client_id,
            "grant_type": "authorization_code",
            "code": server.result["code"],
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
        }
    )
    refresh_token = token.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        raise RuntimeError("Spotify did not return a refresh token")
    return refresh_token


def token_request(fields: dict[str, str]) -> dict[str, Any]:
    request = Request(
        TOKEN_URL,
        data=urlencode(fields).encode("ascii"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except HTTPError as error:
        try:
            detail = json.load(error).get("error_description", error.reason)
        except (json.JSONDecodeError, AttributeError):
            detail = error.reason
        raise RuntimeError(f"Spotify token exchange failed: {detail}") from error
    except (URLError, TimeoutError) as error:
        raise RuntimeError(f"Spotify token exchange failed: {error}") from error
    if not isinstance(payload, dict):
        raise RuntimeError("Spotify returned an invalid token response")
    return payload


def read_response(connection: Any) -> tuple[int, bytes]:
    deadline = time.monotonic() + USB_RESPONSE_TIMEOUT
    buffer = bytearray()
    while time.monotonic() < deadline:
        chunk = connection.read(4096)
        if chunk:
            buffer.extend(chunk)
        position = buffer.find(USB_MAGIC)
        if position < 0:
            if len(buffer) > USB_NOISE_LIMIT:
                raise RuntimeError(
                    "The device returned too much USB output before its protocol response"
                )
            continue
        if position > USB_NOISE_LIMIT:
            raise RuntimeError(
                "The device returned too much USB output before its protocol response"
            )
        if position:
            del buffer[:position]
        if len(buffer) < USB_HEADER_LENGTH:
            continue
        magic, version, status, header_length, payload_length, expected_crc = USB_HEADER.unpack(
            buffer[:USB_HEADER_LENGTH]
        )
        if magic != USB_MAGIC:
            raise RuntimeError("The device returned an invalid USB protocol response")
        if version != USB_PROTOCOL_VERSION:
            raise RuntimeError(f"The device returned unsupported USB protocol version {version}")
        if header_length != USB_HEADER_LENGTH or payload_length > USB_MAX_PAYLOAD:
            raise RuntimeError("The device returned an invalid USB protocol length")
        frame_length = USB_HEADER_LENGTH + payload_length
        if len(buffer) < frame_length:
            continue
        payload = bytes(buffer[USB_HEADER_LENGTH:frame_length])
        actual_crc = zlib.crc32(payload) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            raise RuntimeError("The device returned a USB integrity error")
        return status, payload
    raise RuntimeError(
        "The device did not answer the Spotify-only USB protocol. Run `mise run flash` and retry."
    )


def send_frame(connection: Any, command: int, payload: bytes | bytearray) -> tuple[int, bytes]:
    if len(payload) > USB_MAX_PAYLOAD:
        raise ValueError("USB protocol payload is too large")
    header = USB_HEADER.pack(
        USB_MAGIC,
        USB_PROTOCOL_VERSION,
        command,
        USB_HEADER_LENGTH,
        len(payload),
        zlib.crc32(payload) & 0xFFFFFFFF,
    )
    connection.write(USB_START_BYTE + header + payload)
    connection.flush()
    return read_response(connection)


def open_connection(arguments: list[str]) -> Any:
    return open_serial(serial_factory(arguments))


def query_state(connection: Any) -> int:
    connection.reset_input_buffer()
    status, payload = send_frame(connection, USB_QUERY_COMMAND, b"")
    if status != USB_STATUS_OK:
        raise RuntimeError(f"The device rejected the USB state query with status {status}")
    if len(payload) != 1 or payload[0] not in {
        USB_STATE_NONE,
        USB_STATE_WIFI_ONLY,
        USB_STATE_COMPLETE,
    }:
        raise RuntimeError("The device returned an invalid credential state")
    return payload[0]


def store_spotify(arguments: list[str], client_id: str, refresh_token: str) -> None:
    client_bytes = bytearray(client_id.encode("utf-8"))
    refresh_bytes = bytearray(refresh_token.encode("utf-8"))
    payload = bytearray()
    connection = None
    try:
        payload.extend(struct.pack("<HH", len(client_bytes), len(refresh_bytes)))
        payload.extend(client_bytes)
        payload.extend(refresh_bytes)
        connection = open_connection(arguments)
        state = query_state(connection)
        if state == USB_STATE_NONE:
            raise RuntimeError(
                "The device has no WiFi credentials. Complete captive-portal setup first"
            )
        status, response = send_frame(connection, USB_STORE_SPOTIFY_COMMAND, payload)
        if status == USB_STATUS_NO_CREDENTIALS:
            raise RuntimeError(
                "The device has no WiFi credentials. Complete captive-portal setup first"
            )
        if status != USB_STATUS_OK or response:
            raise RuntimeError(
                f"The device rejected Spotify credential storage with status {status}"
            )
    finally:
        for value in (client_bytes, refresh_bytes, payload):
            value[:] = b"\x00" * len(value)
        if connection is not None:
            connection.close()


def main() -> int:
    try:
        callback_port = int(os.environ.get("BOP_OAUTH_PORT", str(DEFAULT_CALLBACK_PORT)))
        if not 1 <= callback_port <= 65535:
            raise ValueError("BOP_OAUTH_PORT must be from 1 through 65535")
        redirect_uri = f"http://{CALLBACK_HOST}:{callback_port}{CALLBACK_PATH}"
        connection = open_connection(sys.argv)
        try:
            state = query_state(connection)
        finally:
            connection.close()
        if state == USB_STATE_NONE:
            raise RuntimeError(
                "The device has no WiFi credentials. Complete captive-portal setup first"
            )

        require_legal_agreement()
        print_dashboard_steps(redirect_uri)
        client_id = prompt_value("Spotify Client ID", os.environ.get("BOP_CLIENT_ID", ""))
        make_sure_value_fits("Spotify Client ID", client_id, 64)
        refresh_token = authorize(client_id, redirect_uri, callback_port)
        make_sure_value_fits("Spotify refresh token", refresh_token, 1023)
        store_spotify(sys.argv, client_id, refresh_token)
        print("Provisioning is complete. The device acknowledged the write and restarted.")
    except (EOFError, OSError, RuntimeError, TimeoutError, ValueError) as error:
        print(f"provisioning failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Canceled.", file=sys.stderr)
        raise SystemExit(130)
