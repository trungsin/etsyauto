#!/usr/bin/env python3
# Run with: python3 scripts/chatgpt-oauth-login.py
"""ChatGPT OAuth login — one-time setup for gpt-image-2 via Plus subscription.

Since this runs on a remote server, use SSH port forwarding so the OAuth
callback (localhost:1455) reaches the server:

  # On your LOCAL machine — forward local :1455 to server :1455:
  ssh -R 1455:localhost:1455 lesin@<server-ip>

  # Then on the SERVER:
  cd /home/lesin/etsyauto/backend
  python scripts/chatgpt-oauth-login.py

  # Open the printed URL in your browser → login → "Authentication Successful!"
  # Token saved to ~/.codex/auth.json

Port of github.com/therichardngai-code/gpt-image-2-pro-max chatgpt_oauth_login.py
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html as html_lib
import http.server
import secrets
import sys
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.clients.chatgpt_oauth_image_client import (
    OPENAI_AUTH_URL,
    OPENAI_CLIENT_ID,
    OPENAI_REDIRECT_URI,
    OPENAI_SCOPES,
    OPENAI_TOKEN_URL,
    OAuthSession,
    _codex_auth_path,
    _media_tools_token_path,
    has_session,
    load_session,
    save_session,
)

CALLBACK_PORT = 1455


def _pkce() -> tuple[str, str]:
    raw = secrets.token_bytes(64)
    verifier = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _auth_url(challenge: str, state: str) -> str:
    params = {
        "client_id": OPENAI_CLIENT_ID,
        "redirect_uri": OPENAI_REDIRECT_URI,
        "response_type": "code",
        "scope": OPENAI_SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "codex_cli_simplified_flow": "true",
        "id_token_add_organizations": "true",
        "originator": "pi",
    }
    return OPENAI_AUTH_URL + "?" + urllib.parse.urlencode(params)


class _Handler(http.server.BaseHTTPRequestHandler):
    server_state: str = ""
    codes: list = []
    errors: list = []

    def log_message(self, *_):
        pass

    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/auth/callback":
            self._send(404, b"<h2>Not Found</h2>")
            return
        q = urllib.parse.parse_qs(parsed.query)
        state = (q.get("state") or [""])[0]
        if state != self.server_state:
            self._send(400, b"<h2>State mismatch</h2>")
            self.errors.append("state mismatch")
            return
        code = (q.get("code") or [""])[0]
        if not code:
            err = (q.get("error") or ["no code"])[0]
            self._send(400, f"<h2>Auth failed: {html_lib.escape(err)}</h2>".encode())
            self.errors.append(err)
            return
        self._send(200, b"<h2>Authentication Successful! You can close this tab.</h2>")
        self.codes.append(code)

    def _send(self, status: int, body: bytes):
        self.send_response(status)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _wait_for_callback(state: str, timeout: int) -> str:
    handler = _Handler
    handler.server_state = state
    handler.codes = []
    handler.errors = []
    httpd = http.server.HTTPServer(("127.0.0.1", CALLBACK_PORT), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    deadline = time.time() + timeout
    try:
        while time.time() < deadline:
            if handler.codes:
                return handler.codes[0]
            if handler.errors:
                raise RuntimeError(f"OAuth error: {handler.errors[0]}")
            time.sleep(0.25)
        raise TimeoutError("OAuth callback timed out")
    finally:
        httpd.shutdown()


def _exchange_and_save(code: str, verifier: str) -> int:
    print("\nExchanging code for tokens...")
    resp = requests.post(
        OPENAI_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "client_id": OPENAI_CLIENT_ID,
            "code": code,
            "redirect_uri": OPENAI_REDIRECT_URI,
            "code_verifier": verifier,
        },
        timeout=30,
    )
    if resp.status_code != 200:
        print(f"Token exchange failed ({resp.status_code}): {resp.text[:300]}", file=sys.stderr)
        return 3

    token_data = resp.json()
    session = OAuthSession(
        access_token=token_data.get("access_token", ""),
        refresh_token=token_data.get("refresh_token", ""),
        expires_at=time.time() + int(token_data.get("expires_in", 0)),
        scope=token_data.get("scope", ""),
    )
    if not session.access_token:
        print("Missing access_token in response", file=sys.stderr)
        return 3

    save_session(session)
    expires_min = int(token_data.get("expires_in", 0)) // 60
    print()
    print("Login successful!")
    print(f"  Saved to: {_codex_auth_path()}")
    print(f"  Also at:  {_media_tools_token_path()}")
    print(f"  Token expires in ~{expires_min} minutes (auto-refreshed via refresh_token)")
    print()
    print("You can now use the artwork Refine step with gpt-image-2!")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="ChatGPT OAuth login for artwork pipeline")
    p.add_argument("--force", action="store_true", help="Re-login even if already logged in")
    p.add_argument("--timeout", type=int, default=300, help="Seconds to wait for auto callback")
    p.add_argument("--manual", action="store_true",
                   help="Manual mode: no callback server — paste the redirect URL yourself")
    args = p.parse_args()

    if not args.force and has_session():
        s = load_session()
        print("Already logged in!")
        print(f"  Token: {_codex_auth_path()}")
        if s and s.account_id:
            print(f"  Account: {s.account_id}")
        if s and s.plan_type:
            print(f"  Plan: {s.plan_type}")
        print("Use --force to re-login.")
        return 0

    verifier, challenge = _pkce()
    state = base64.urlsafe_b64encode(secrets.token_bytes(16)).rstrip(b"=").decode("ascii")
    url = _auth_url(challenge, state)

    print("=" * 60)
    print("ChatGPT OAuth Login")
    print("=" * 60)
    print()
    print("Open this URL in your browser:")
    print()
    print(f"  {url}")
    print()

    if args.manual:
        # ── Manual mode: user pastes the redirect URL ──────────────────────
        print("After login, the browser will redirect to:")
        print("  http://localhost:1455/auth/callback?code=...&state=...")
        print()
        print("The page will show an error (no server) — that's OK.")
        print("Copy the FULL URL from the browser's address bar and paste it here.")
        print()
        try:
            redirect_url = input("Paste redirect URL: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.", file=sys.stderr)
            return 2

        parsed = urllib.parse.urlparse(redirect_url)
        q = urllib.parse.parse_qs(parsed.query)
        got_state = (q.get("state") or [""])[0]
        if got_state != state:
            print(f"State mismatch — expected {state!r}, got {got_state!r}.\n"
                  "Make sure you pasted the URL from the correct login attempt.", file=sys.stderr)
            return 2
        code = (q.get("code") or [""])[0]
        if not code:
            err = (q.get("error") or ["no code in URL"])[0]
            print(f"No auth code in URL: {err}", file=sys.stderr)
            return 2

        return _exchange_and_save(code, verifier)

    else:
        # ── Auto mode: local callback server ───────────────────────────────
        print(f"Waiting for callback on http://127.0.0.1:{CALLBACK_PORT}/auth/callback ...")
        print(f"(timeout: {args.timeout}s)")
        print()
        print("TIP: If you get 'Address already in use', run with --manual instead:")
        print("  python3 scripts/chatgpt-oauth-login.py --manual")
        try:
            code = _wait_for_callback(state, args.timeout)
        except (TimeoutError, RuntimeError) as e:
            print(f"\nLogin failed: {e}", file=sys.stderr)
            return 2
        return _exchange_and_save(code, verifier)


if __name__ == "__main__":
    sys.exit(main())
