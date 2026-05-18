"""HTTP client for the Compresh /v1/tul1 server-side enhancement endpoint.

`compresh-mcp >= 0.2.0` runs the open-source tulbase compression core
locally (LexRank summarization, Protection Zone, modality elision) and
optionally enhances the result via the paid `/v1/tul1` endpoint when a
valid Compresh API key is configured.

The TUL 1.0 layers (Q-protective ranking, epistemic markers, semantic
store) live exclusively on the Compresh server in 0.2.0+. This is a
deliberate architectural change from 0.1.0, which shipped those layers
in the client package — that 0.1.0 distribution leaked paid features
into the local install. See `archive/0.1.0-tul1/README.md` for context.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

import httpx

logger = logging.getLogger("compresh-mcp.tul1")

DEFAULT_TIMEOUT = 15.0
DEFAULT_API_BASE = "https://api.compre.sh"


@dataclass(slots=True)
class Tul1Result:
    """Successful /v1/tul1 response, unpacked for the MCP server to consume."""

    ok: bool
    applied: bool
    tier: Optional[str]
    compresh_md: str
    raw_tail: list[dict[str, Any]]
    n_compressed_turns: int
    n_total: int
    saving_chars: int
    saving_tokens: int
    session_id: str
    protection_mode: str
    fee_cents: float
    tier_label: Optional[str]
    version: str


class Tul1Error(Exception):
    """Base class for /v1/tul1 client errors."""


class Tul1PaymentRequired(Tul1Error):
    """HTTP 402 — caller's tier / budget does not entitle TUL 1.0 access."""

    def __init__(self, message: str, *, your_tier: str, budget_cents: int):
        super().__init__(message)
        self.your_tier = your_tier
        self.budget_cents = budget_cents


class Tul1NetworkError(Tul1Error):
    """Transport-level failure — caller falls back to local result."""


class Tul1ServerError(Tul1Error):
    """Server returned 5xx — caller falls back to local result."""


async def call_v1_tul1(
    *,
    api_key: str,
    session_id: str,
    messages: list[dict[str, Any]],
    protection_mode: str = "balanced",
    provider_hint: Optional[str] = None,
    model_hint: Optional[str] = None,
    api_base: str = DEFAULT_API_BASE,
    timeout: float = DEFAULT_TIMEOUT,
) -> Tul1Result:
    """Call the Compresh /v1/tul1 server-side enhancement endpoint.

    Raises:
        Tul1PaymentRequired: HTTP 402 — caller's tier / budget rejected.
            The MCP server should surface this in the tool response so
            the user knows why they're not getting enhanced compression.
        Tul1NetworkError: transport failure. Caller falls back to local
            tulbase result silently (degraded mode).
        Tul1ServerError: HTTP 5xx. Caller falls back to local result.

    Network and server errors are NOT fatal — paid users always have a
    valid local fallback because the open-source tulbase core ships in
    the same package.
    """
    url = f"{api_base.rstrip('/')}/v1/tul1"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body: dict[str, Any] = {
        "session_id": session_id,
        "messages": messages,
        "protection_mode": protection_mode,
    }
    if provider_hint:
        body["provider_hint"] = provider_hint
    if model_hint:
        body["model_hint"] = model_hint

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, headers=headers, json=body)
    except httpx.HTTPError as e:
        logger.warning("[/v1/tul1] network error: %s — falling back to local", e)
        raise Tul1NetworkError(f"network: {type(e).__name__}: {e}") from e

    if resp.status_code == 402:
        try:
            data = resp.json()
        except Exception:
            data = {}
        raise Tul1PaymentRequired(
            data.get("error", "TUL 1.0 requires Pro subscription or budget > $0"),
            your_tier=data.get("your_tier", "unknown"),
            budget_cents=data.get("your_budget_cents", 0),
        )

    if 500 <= resp.status_code < 600:
        logger.warning(
            "[/v1/tul1] server %d — falling back to local: %s",
            resp.status_code, resp.text[:200],
        )
        raise Tul1ServerError(f"HTTP {resp.status_code}: {resp.text[:200]}")

    if resp.status_code != 200:
        # 4xx other than 402 — log and treat as fall-back. Don't crash.
        logger.warning(
            "[/v1/tul1] unexpected %d — falling back to local: %s",
            resp.status_code, resp.text[:200],
        )
        raise Tul1NetworkError(f"HTTP {resp.status_code}: {resp.text[:200]}")

    data = resp.json() if resp.content else {}
    return Tul1Result(
        ok=bool(data.get("ok")),
        applied=bool(data.get("applied")),
        tier=data.get("tier"),
        compresh_md=data.get("compresh_md") or "",
        raw_tail=list(data.get("raw_tail") or []),
        n_compressed_turns=int(data.get("n_compressed_turns") or 0),
        n_total=int(data.get("n_total") or 0),
        saving_chars=int(data.get("saving_chars") or 0),
        saving_tokens=int(data.get("saving_tokens") or 0),
        session_id=data.get("session_id") or session_id,
        protection_mode=data.get("protection_mode") or protection_mode,
        fee_cents=float(data.get("fee_cents") or 0.0),
        tier_label=data.get("tier_label"),
        version=data.get("version") or "tul1-v?",
    )
