"""ChatGPT OAuth image client — uses ChatGPT Plus subscription (no API billing needed).

Ports the image-generation logic from github.com/therichardngai-code/gpt-image-2-pro-max.
Auth via OAuth token stored in ~/.codex/auth.json (shared with Codex CLI).

One-time login required: run `python scripts/chatgpt_oauth_login.py` from the backend dir.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import requests as http_requests
from PIL import Image

logger = logging.getLogger(__name__)

# ── OAuth constants (ported verbatim from goclaw/oauth/openai.go) ──────────
OPENAI_CLIENT_ID    = "app_EMoamEEZ73f0CkXaXp7hrann"
OPENAI_TOKEN_URL    = "https://auth.openai.com/oauth/token"
OPENAI_AUTH_URL     = "https://auth.openai.com/oauth/authorize"
OPENAI_SCOPES       = "openid profile email offline_access api.connectors.read api.connectors.invoke"
OPENAI_REDIRECT_URI = "http://localhost:1455/auth/callback"
DEFAULT_API_BASE    = "https://chatgpt.com/backend-api"
REFRESH_MARGIN_SEC  = 5 * 60  # refresh if expiry within 5 minutes

# ── Image generation constants ─────────────────────────────────────────────
DEFAULT_PARENT_MODEL = "gpt-5.4"
DEFAULT_IMAGE_MODEL  = "gpt-image-2"
TIMEOUT_SECONDS      = 600

ARTWORK_REFINE_PROMPT = (
    "This is a design artwork cropped from a product mockup. "
    "Remove any mockup context (fabric texture, product shape, background, shadows). "
    "Clean and sharpen the edges of the design. "
    "Output only the design on a perfectly clean white background, "
    "ready for background removal in the next step. "
    "Keep the design artwork exactly as-is, do not alter colors or shapes."
)


# ── Token storage ──────────────────────────────────────────────────────────

@dataclass
class OAuthSession:
    access_token: str
    refresh_token: str
    expires_at: float
    api_base: str = DEFAULT_API_BASE
    account_id: str = ""
    plan_type: str = ""
    scope: str = ""


def _codex_auth_path() -> Path:
    return Path.home() / ".codex" / "auth.json"


def _media_tools_token_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "media-tools" / "chatgpt_oauth.json"


def _decode_jwt_exp(token: str) -> float:
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return 0.0
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        return float(data.get("exp", 0))
    except Exception:
        return 0.0


def _load_from_codex() -> OAuthSession | None:
    p = _codex_auth_path()
    if not p.exists():
        return None
    try:
        with p.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return None
    tokens = raw.get("tokens") or {}
    access = tokens.get("access_token") or ""
    refresh = tokens.get("refresh_token") or ""
    if not access:
        return None
    return OAuthSession(
        access_token=access,
        refresh_token=refresh,
        expires_at=_decode_jwt_exp(access),
        api_base=DEFAULT_API_BASE,
        account_id=tokens.get("account_id") or "",
    )


def _load_from_media_tools() -> OAuthSession | None:
    p = _media_tools_token_path()
    if not p.exists():
        return None
    try:
        with p.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        return OAuthSession(
            access_token=raw.get("access_token", ""),
            refresh_token=raw.get("refresh_token", ""),
            expires_at=float(raw.get("expires_at", 0)),
            api_base=raw.get("api_base", DEFAULT_API_BASE),
            account_id=raw.get("account_id", ""),
            plan_type=raw.get("plan_type", ""),
            scope=raw.get("scope", ""),
        )
    except Exception:
        return None


def load_session() -> OAuthSession | None:
    return _load_from_codex() or _load_from_media_tools()


def save_session(s: OAuthSession) -> None:
    # Save to ~/.codex/auth.json (shared with Codex CLI)
    codex = _codex_auth_path()
    try:
        if codex.exists():
            with codex.open("r", encoding="utf-8") as f:
                raw = json.load(f)
        else:
            codex.parent.mkdir(parents=True, exist_ok=True)
            raw = {"OPENAI_API_KEY": None, "tokens": {}}
        tokens = raw.setdefault("tokens", {})
        if s.access_token:
            tokens["access_token"] = s.access_token
        if s.refresh_token:
            tokens["refresh_token"] = s.refresh_token
        raw["last_refresh"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        with codex.open("w", encoding="utf-8") as f:
            json.dump(raw, f, indent=2)
        os.chmod(codex, 0o600)
    except OSError:
        pass

    # Save to media-tools cache
    p = _media_tools_token_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(asdict(s), f, indent=2)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def has_session() -> bool:
    s = load_session()
    return s is not None and bool(s.access_token)


def get_access_token() -> str:
    s = load_session()
    if s is None:
        raise RuntimeError(
            "No ChatGPT OAuth session found. Run the login script first:\n"
            "  cd backend && python scripts/chatgpt_oauth_login.py\n"
            f"  Expected token at: {_codex_auth_path()}"
        )
    now = time.time()
    if s.access_token and (s.expires_at - now) > REFRESH_MARGIN_SEC:
        return s.access_token

    if not s.refresh_token:
        return s.access_token or ""

    try:
        resp = http_requests.post(
            OPENAI_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": OPENAI_CLIENT_ID,
                "refresh_token": s.refresh_token,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            logger.warning("Token refresh failed %s — using existing token", resp.status_code)
            return s.access_token
        token_data = resp.json()
        s.access_token = token_data.get("access_token", "")
        if rt := token_data.get("refresh_token"):
            s.refresh_token = rt
        s.expires_at = now + int(token_data.get("expires_in", 0))
        save_session(s)
        return s.access_token
    except Exception as exc:
        logger.warning("Token refresh exception — using existing: %s", exc)
        return s.access_token


# ── Image generation / editing ─────────────────────────────────────────────

def _build_request_body(prompt: str, ref_images: list[dict] | None = None,
                        image_model: str = DEFAULT_IMAGE_MODEL,
                        parent_model: str = DEFAULT_PARENT_MODEL,
                        size: str = "1024x1024",
                        output_format: str = "png") -> dict:
    user_content: list[dict] = [{"type": "input_text", "text": prompt}]
    for ref in ref_images or []:
        b64 = base64.b64encode(ref["bytes"]).decode("ascii")
        user_content.append({
            "type": "input_image",
            "image_url": f"data:{ref['mime']};base64,{b64}",
        })

    action = "edit" if ref_images else "generate"
    return {
        "model": parent_model,
        "stream": True,
        "store": False,
        "instructions": (
            "Generate an image matching the user's description using the "
            "image_generation tool. Return only the image; do not describe it in text."
        ),
        "input": [{"role": "user", "content": user_content}],
        "tools": [{
            "type": "image_generation",
            "action": action,
            "model": image_model,
            "output_format": output_format,
            "size": size,
        }],
        "tool_choice": {"type": "image_generation"},
    }


def _parse_sse(stream) -> bytes:
    b64 = ""
    for raw_line in stream.iter_lines():
        if not raw_line:
            continue
        if isinstance(raw_line, bytes):
            try:
                raw_line = raw_line.decode("utf-8")
            except UnicodeDecodeError:
                continue
        if not raw_line.startswith("data: "):
            continue
        payload = raw_line[6:]
        if payload == "[DONE]":
            break
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            continue

        etype = event.get("type", "")
        if etype == "response.output_item.done":
            item = event.get("item") or {}
            if item.get("type") == "image_generation_call" and item.get("result"):
                b64 = item["result"]
        elif etype == "response.completed":
            for it in (event.get("response") or {}).get("output") or []:
                if it.get("type") == "image_generation_call" and it.get("result"):
                    b64 = it["result"]
        elif etype in ("response.failed", "error"):
            err = event.get("response", {}).get("error") or event.get("error") or {}
            msg = err.get("message") or err.get("code") or "unknown"
            raise RuntimeError(f"ChatGPT API error: {msg}")

    if not b64:
        raise RuntimeError("No image received in ChatGPT SSE stream")
    return base64.b64decode(b64)


class ChatGPTOAuthImageClient:
    """Generate/edit images using ChatGPT Plus OAuth — gpt-image-2 quality, no API billing."""

    def edit_for_artwork(self, image_bytes: bytes, prompt: str | None = None) -> bytes:
        """Refine a cropped artwork image.

        Args:
            image_bytes: PNG bytes of the cropped design.
            prompt: Override the default cleaning instruction.

        Returns:
            PNG bytes of the refined image.

        Raises:
            RuntimeError: If OAuth session missing or API call fails.
        """
        token = get_access_token()
        if not token:
            raise RuntimeError(
                "ChatGPT OAuth token missing — run: python scripts/chatgpt_oauth_login.py"
            )

        # chatgpt.com/backend-api/codex/responses only supports "1024x1024" for edit.
        # Pad portrait/landscape to square so the full design is always visible.
        try:
            src = Image.open(BytesIO(image_bytes)).convert("RGBA")
            orig_w, orig_h = src.size
            if orig_w != orig_h:
                side = max(orig_w, orig_h)
                canvas = Image.new("RGBA", (side, side), (255, 255, 255, 255))
                canvas.paste(src, ((side - orig_w) // 2, 0), src)  # centre-x, top-align
                buf = BytesIO()
                canvas.save(buf, format="PNG")
                padded_bytes = buf.getvalue()
                logger.info("ChatGPT OAuth: padded %dx%d → %dx%d square", orig_w, orig_h, side, side)
            else:
                padded_bytes = image_bytes
        except Exception as exc:
            logger.warning("ChatGPT OAuth: padding failed (%s), sending as-is", exc)
            padded_bytes = image_bytes

        instruction = prompt or ARTWORK_REFINE_PROMPT
        ref_images = [{"bytes": padded_bytes, "mime": "image/png"}]
        body = _build_request_body(
            prompt=instruction,
            ref_images=ref_images,
            image_model=DEFAULT_IMAGE_MODEL,
        )

        endpoint = f"{DEFAULT_API_BASE}/codex/responses"
        logger.info("ChatGPT OAuth: calling %s (gpt-image-2 edit)", endpoint)

        resp = http_requests.post(
            endpoint,
            data=json.dumps(body),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
                "OpenAI-Beta": "responses=v1",
            },
            stream=True,
            timeout=TIMEOUT_SECONDS,
        )
        if resp.status_code != 200:
            try:
                err_body = resp.json()
            except Exception:
                err_body = {}
            resp.close()
            # When the token is invalidated server-side, clear it so has_session() returns
            # False on subsequent calls — prevents retrying with a known-bad token.
            if resp.status_code == 401 and err_body.get("error", {}).get("code") == "token_invalidated":
                logger.warning("ChatGPT OAuth: token invalidated by server — clearing access token")
                try:
                    s = load_session()
                    if s:
                        s.access_token = ""
                        save_session(s)
                except Exception as _clr_exc:
                    logger.warning("ChatGPT OAuth: could not clear session: %s", _clr_exc)
            snippet = (str(err_body)[:400] if err_body else (resp.text[:400] if hasattr(resp, "text") else ""))
            raise RuntimeError(f"ChatGPT API {resp.status_code}: {snippet}")

        try:
            result = _parse_sse(resp)
            logger.info(
                "ChatGPT OAuth: image edit success — %d → %d bytes",
                len(image_bytes), len(result),
            )
            return result
        finally:
            resp.close()
