"""Compresh API key validation + usage telemetry.

The MCP server validates the user's COMPRESH_API_KEY against the
Compresh production API at startup, then reports per-session savings
back so the dashboard reflects local usage.

The Compresh API base URL is configurable via COMPRESH_API_BASE
(default: https://api.compre.sh) — useful for staging/dev environments.

Validation flow:

    1. Read COMPRESH_API_KEY from environment.
    2. If missing/empty -> raise NoApiKey (caller triggers onboarding flow).
    3. POST to /v1/auth/verify with Bearer token.
    4. If 200 -> cache validated key + user info for session lifetime.
    5. If 401/403 -> raise InvalidApiKey (caller may re-prompt).
    6. If network failure -> raise AuthNetworkError (caller may proceed
       in offline mode with a warning).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger("compresh-mcp.auth")

DEFAULT_API_BASE = os.environ.get("COMPRESH_API_BASE", "https://api.compre.sh")
DEFAULT_TIMEOUT = float(os.environ.get("COMPRESH_AUTH_TIMEOUT", "10"))


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AuthError(Exception):
    """Base class for auth failures."""


class NoApiKey(AuthError):
    """COMPRESH_API_KEY environment variable missing or empty."""


class InvalidApiKey(AuthError):
    """API key was rejected by the Compresh server (401/403)."""


class AuthNetworkError(AuthError):
    """Network or unexpected error while contacting Compresh server."""


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AuthResult:
    """Outcome of a successful key validation."""

    ok: bool
    api_key: str
    email: Optional[str] = None
    tier: Optional[str] = None  # "free" | "pro"
    free_credit_remaining: Optional[float] = None
    budget_remaining: Optional[float] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_api_key() -> Optional[str]:
    """Read COMPRESH_API_KEY from environment, return None if missing/empty."""
    key = (os.environ.get("COMPRESH_API_KEY") or "").strip()
    return key or None


def verify_api_key(
    api_key: Optional[str] = None,
    *,
    api_base: str = DEFAULT_API_BASE,
    timeout: float = DEFAULT_TIMEOUT,
) -> AuthResult:
    """Validate an API key against the Compresh server.

    Raises:
        NoApiKey: key is missing/empty.
        InvalidApiKey: key was rejected (401/403).
        AuthNetworkError: transport-level failure (caller decides whether
            to proceed in offline mode).
    """
    key = api_key or get_api_key()
    if not key:
        raise NoApiKey("COMPRESH_API_KEY environment variable is not set")

    url = f"{api_base.rstrip('/')}/v1/auth/verify"
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url, headers=headers)
    except httpx.HTTPError as e:
        logger.warning("auth network error: %s", e)
        raise AuthNetworkError(f"network error: {type(e).__name__}: {e}") from e

    if resp.status_code in (401, 403):
        logger.info("auth rejected: %d", resp.status_code)
        raise InvalidApiKey(f"server rejected key (HTTP {resp.status_code})")

    if resp.status_code != 200:
        logger.warning("auth unexpected status: %d", resp.status_code)
        raise AuthNetworkError(f"unexpected HTTP {resp.status_code}")

    data = resp.json() if resp.content else {}
    return AuthResult(
        ok=True,
        api_key=key,
        email=data.get("email"),
        tier=data.get("tier"),
        free_credit_remaining=data.get("free_credit_remaining"),
        budget_remaining=data.get("budget_remaining"),
    )


def report_usage(
    api_key: str,
    *,
    session_id: str,
    saved_input_tokens: int,
    saved_chars: int,
    n_turns: int,
    n_compressed_entries: int,
    provider_hint: Optional[str] = None,
    model_hint: Optional[str] = None,
    api_base: str = DEFAULT_API_BASE,
    timeout: float = DEFAULT_TIMEOUT,
) -> bool:
    """Send a per-session usage report to Compresh for billing/telemetry.

    Returns True on 2xx, False otherwise. Failures are non-fatal — local
    compression continues, telemetry catches up at the next successful
    report.
    """
    url = f"{api_base.rstrip('/')}/v1/usage/report"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "session_id": session_id,
        "saved_input_tokens": saved_input_tokens,
        "saved_chars": saved_chars,
        "n_turns": n_turns,
        "n_compressed_entries": n_compressed_entries,
        "provider_hint": provider_hint,
        "model_hint": model_hint,
        "source": "compresh-mcp",
    }

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, headers=headers, json=payload)
        if 200 <= resp.status_code < 300:
            return True
        logger.warning("usage report rejected: %d", resp.status_code)
    except httpx.HTTPError as e:
        logger.warning("usage report network error: %s", e)
    return False
