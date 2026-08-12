#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mark Phelps
# SPDX-License-Identifier: Apache-2.0

"""Authorize Spotify and write WiFi and OAuth values to the device NVS partition."""

from __future__ import annotations

import base64
import csv
from getpass import getpass
import hashlib
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import platform
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen
import webbrowser

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
SCOPES = "user-read-playback-state user-modify-playback-state"
CALLBACK_HOST = "127.0.0.1"
CALLBACK_PATH = "/callback"
DEFAULT_CALLBACK_PORT = 43821
NVS_OFFSET = "0x9000"
NVS_SIZE = "0x6000"
LEGAL_DOCUMENTS = ("EULA.md", "PRIVACY.md")
LEGAL_AGREEMENT_PROMPT = "Type I AGREE to accept EULA.md and PRIVACY.md before Spotify authorization: "


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
        raise RuntimeError("Provisioning stopped. Type I AGREE exactly to start Spotify authorization.")


def command_output(command: list[str]) -> str:
    try:
        return subprocess.check_output(command, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def mac_wifi_interfaces() -> list[str]:
    output = command_output(["networksetup", "-listallhardwareports"])
    interfaces: list[str] = []
    wifi_port = False
    for line in output.splitlines():
        if line.startswith("Hardware Port:"):
            wifi_port = line.partition(":")[2].strip() in {"Wi-Fi", "AirPort"}
        elif wifi_port and line.startswith("Device:"):
            interfaces.append(line.partition(":")[2].strip())
    return interfaces


def current_ssid() -> str:
    system = platform.system()
    if system == "Darwin":
        prefix = "Current Wi-Fi Network: "
        for interface in mac_wifi_interfaces():
            output = command_output(["networksetup", "-getairportnetwork", interface])
            if output.startswith(prefix):
                return output[len(prefix) :]
        return ""
    if system == "Linux":
        output = command_output(["nmcli", "-t", "-f", "ACTIVE,SSID", "device", "wifi"])
        for line in output.splitlines():
            if line.startswith("yes:"):
                return line.split(":", 1)[1].replace(r"\:", ":")
    if system == "Windows":
        output = command_output(["netsh", "wlan", "show", "interfaces"])
        for line in output.splitlines():
            key, separator, value = line.partition(":")
            if separator and key.strip() == "SSID":
                return value.strip()
    return ""


def prompt_value(label: str, default: str = "", *, secret: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    value = getpass(f"{label}{suffix}: ") if secret else input(f"{label}{suffix}: ").strip()
    return value or default


def make_sure_value_fits(label: str, value: str, maximum_bytes: int, *, allow_empty: bool = False) -> None:
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
        message = "Authorization complete. You can close this page." if successful else "Authorization failed. Return to the terminal."
        body = f"<!doctype html><meta charset=utf-8><title>spot</title><p>{message}</p>".encode()
        self.send_response(HTTPStatus.OK if successful else HTTPStatus.BAD_REQUEST)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        _ = (format, args)


def authorize(client_id: str, redirect_uri: str, port: int) -> str:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode("ascii")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
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
    if os.environ.get("SPOT_NO_BROWSER") != "1":
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


def write_nvs_csv(path: Path, ssid: str, password: str, client_id: str, refresh_token: str) -> None:
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.writer(output)
        writer.writerow(["key", "type", "encoding", "value"])
        writer.writerow(["spot", "namespace", "", ""])
        writer.writerow(["wifi_ssid", "data", "string", ssid])
        writer.writerow(["wifi_pass", "data", "string", password])
        writer.writerow(["client_id", "data", "string", client_id])
        writer.writerow(["refresh_tok", "data", "string", refresh_token])
    path.chmod(0o600)


def idf_path() -> Path:
    configured = os.environ.get("SPOT_IDF_PATH")
    return Path(configured).expanduser() if configured else Path.home() / ".local/share/esp-idf"


def generate_nvs(csv_path: Path, image_path: Path) -> None:
    root = idf_path()
    generator = root / "components/nvs_flash/nvs_partition_generator/nvs_partition_gen.py"
    arguments = [str(generator), "generate", str(csv_path), str(image_path), NVS_SIZE]
    if os.name == "nt":
        command = f'call "{root / "export.bat"}" >nul && python {subprocess.list2cmdline(arguments)}'
        subprocess.run(["cmd.exe", "/d", "/s", "/c", command], check=True)
    else:
        script = '. "$1" >/dev/null && shift && exec python "$@"'
        subprocess.run(["bash", "-c", script, "idf-wrapper", str(root / "export.sh"), *arguments], check=True)
    image_path.chmod(0o600)
    expected_size = int(NVS_SIZE, 0)
    if image_path.stat().st_size != expected_size:
        raise RuntimeError(f"NVS image must be exactly {expected_size} bytes")


def detect_port() -> str:
    result = subprocess.run(
        [sys.executable, str(project_root() / "tools/detect_port.py")],
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def flash_nvs(image_path: Path) -> None:
    esptool = shutil.which("esptool") or shutil.which("esptool.py")
    if esptool is None:
        raise RuntimeError("esptool is unavailable. Run `mise install` first")
    subprocess.run(
        [esptool, "--chip", "esp32s3", "--port", detect_port(), "--after", "hard-reset", "write-flash", NVS_OFFSET, str(image_path)],
        check=True,
    )


def main() -> int:
    try:
        callback_port = int(os.environ.get("SPOT_OAUTH_PORT", str(DEFAULT_CALLBACK_PORT)))
        if not 1 <= callback_port <= 65535:
            raise ValueError("SPOT_OAUTH_PORT must be from 1 through 65535")
        redirect_uri = f"http://{CALLBACK_HOST}:{callback_port}{CALLBACK_PATH}"
        require_legal_agreement()
        print_dashboard_steps(redirect_uri)

        ssid = prompt_value("WiFi SSID", os.environ.get("SPOT_WIFI_SSID", current_ssid()))
        password = prompt_value("WiFi password (input is hidden)", secret=True)
        client_id = prompt_value("Spotify Client ID", os.environ.get("SPOT_CLIENT_ID", ""))
        make_sure_value_fits("WiFi SSID", ssid, 32)
        make_sure_value_fits("WiFi password", password, 64, allow_empty=True)
        make_sure_value_fits("Spotify Client ID", client_id, 64)

        refresh_token = authorize(client_id, redirect_uri, callback_port)
        make_sure_value_fits("Spotify refresh token", refresh_token, 1023)
        with tempfile.TemporaryDirectory(prefix="spot-provision-") as directory:
            temporary = Path(directory)
            csv_path = temporary / "credentials.csv"
            image_path = temporary / "nvs.bin"
            write_nvs_csv(csv_path, ssid, password, client_id, refresh_token)
            generate_nvs(csv_path, image_path)
            flash_nvs(image_path)
        print("Provisioning is complete. The device has restarted.")
    except (EOFError, OSError, RuntimeError, TimeoutError, ValueError, subprocess.CalledProcessError) as error:
        print(f"provisioning failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Canceled.", file=sys.stderr)
        raise SystemExit(130)
