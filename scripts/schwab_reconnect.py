#!/usr/bin/env python
"""One-command Schwab reconnect.

Schwab refresh tokens hard-expire every 7 days, so a browser re-auth is
unavoidable — but it does NOT have to mean copy-pasting a callback URL and
racing a 30-second code expiry. This script:

  1. Spins up a local HTTPS listener on the registered redirect URI
     (https://127.0.0.1:8765/callback by default).
  2. Opens the Schwab login page in your browser.
  3. Catches the redirect, extracts the authorization code, and exchanges it
     for tokens *server-side in milliseconds* — no paste, no expiry race.
  4. Writes the token file the rest of the app already reads.

Usage:
    uv run python scripts/schwab_reconnect.py      (or: make schwab-reconnect)

The browser will warn about the self-signed localhost certificate — click
"Advanced" -> "Proceed to 127.0.0.1 (unsafe)". That is expected and safe: the
cert only secures a loopback connection to your own machine.
"""

from __future__ import annotations

import ipaddress
import ssl
import sys
import time
import webbrowser
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv

# Load .env exactly like the server does (maverick_mcp/api/server.py).
load_dotenv()

from maverick_mcp.providers.schwab.auth import (  # noqa: E402
    SchwabAuthConfig,
    SchwabTokenStore,
    exchange_authorization_code,
)

CERT_DIR = Path(".local")
CERT_PATH = CERT_DIR / "schwab-localhost-cert.pem"
KEY_PATH = CERT_DIR / "schwab-localhost-key.pem"
LISTEN_TIMEOUT_S = 300  # give the login flow 5 minutes

# Populated by the request handler; read by the main loop.
RESULT: dict[str, object] = {"done": False, "ok": False, "error": None}


def ensure_self_signed_cert() -> None:
    """Create a persistent self-signed cert for 127.0.0.1 if absent."""
    if CERT_PATH.exists() and KEY_PATH.exists():
        return

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    CERT_DIR.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    KEY_PATH.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    CERT_PATH.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    KEY_PATH.chmod(0o600)


def make_handler(config: SchwabAuthConfig, store: SchwabTokenStore, callback_path: str):
    class CallbackHandler(BaseHTTPRequestHandler):
        def _reply(self, status: int, title: str, body: str) -> None:
            html = (
                f"<!doctype html><html><head><meta charset='utf-8'>"
                f"<title>{title}</title></head><body style='font-family:system-ui;"
                f"max-width:32rem;margin:4rem auto;text-align:center'>"
                f"<h2>{title}</h2><p>{body}</p></body></html>"
            ).encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path != callback_path:
                self.send_response(404)
                self.end_headers()
                return

            code = parse_qs(parsed.query).get("code", [""])[0]
            if not code:
                self._reply(400, "No authorization code", "Missing ?code= in callback.")
                return

            try:
                token = exchange_authorization_code(config, code)
                store.write(token)
                RESULT["ok"] = True
                self._reply(
                    200,
                    "✅ Schwab reconnected",
                    "Tokens saved. You can close this tab and return to the terminal.",
                )
            except Exception as exc:  # noqa: BLE001
                RESULT["error"] = str(exc)
                self._reply(500, "❌ Token exchange failed", str(exc))
            finally:
                RESULT["done"] = True

        def log_message(self, *_args) -> None:  # silence default request logging
            pass

    return CallbackHandler


def main() -> int:
    try:
        config = SchwabAuthConfig.from_env()
    except Exception as exc:  # noqa: BLE001
        print(f"❌ Schwab config error: {exc}", file=sys.stderr)
        print("   Check SCHWAB_CLIENT_ID / SCHWAB_CLIENT_SECRET in .env", file=sys.stderr)
        return 2

    parsed = urlparse(config.redirect_uri)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8765
    callback_path = parsed.path or "/callback"
    store = SchwabTokenStore(config.token_file)

    ensure_self_signed_cert()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=str(CERT_PATH), keyfile=str(KEY_PATH))

    handler = make_handler(config, store, callback_path)
    httpd = HTTPServer((host, port), handler)
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    httpd.timeout = 1  # so the loop below can check the deadline

    auth_url = config.authorization_url()
    print("── Schwab reconnect ─────────────────────────────────────────")
    print(f"Listening on {config.redirect_uri}")
    print("Opening your browser to log in to Schwab…")
    print("\nIf it doesn't open, paste this into your browser:\n")
    print(f"  {auth_url}\n")
    print("Your browser WILL warn about the certificate for 127.0.0.1 —")
    print('click "Advanced" → "Proceed to 127.0.0.1 (unsafe)". That is expected.')
    print("─────────────────────────────────────────────────────────────")

    try:
        webbrowser.open(auth_url)
    except Exception:
        pass

    deadline = time.time() + LISTEN_TIMEOUT_S
    try:
        while not RESULT["done"] and time.time() < deadline:
            httpd.handle_request()
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 130
    finally:
        httpd.server_close()

    if RESULT["ok"]:
        status = store.status()
        print(f"\n✅ Reconnected. Token expires_at={status.get('expires_at')}")
        print("   Cash and live positions are back. Next daily run will use them.")
        return 0
    if RESULT["error"]:
        print(f"\n❌ Reconnect failed: {RESULT['error']}", file=sys.stderr)
        return 1
    print("\n⏳ Timed out waiting for the Schwab callback. Re-run to try again.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
