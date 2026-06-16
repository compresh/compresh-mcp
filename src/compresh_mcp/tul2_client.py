"""HTTP client for the Compresh /v1/tul2 server-side enhancement endpoint.

`compresh-mcp >= 0.3.0` runs the open-source tulbase compression core
locally (LexRank summarization, Protection Zone, modality elision) and
optionally enhances the result via the paid `/v1/tul2` endpoint when a
valid Compresh API key is configured.

The TUL 2.0 paid layer (query-aware retrieval over full history, role-
preserving render) runs exclusively on the Compresh server. The local
package ships tulbase only — the paid layer never lives client-side.

Endpoint history:
  - 0.1.0 shipped the (now-retired) TUL 1.0 classifiers locally — an
    architectural mistake that leaked paid features. See
    `archive/0.1.0-tul1/README.md`.
  - 0.2.x moved the paid layer server-side behind `/v1/tul1`.
  - 0.3.0 follows the server rename to the canonical `/v1/tul2`
    (TUL 1.0 Q-matrix was retired in the 15 Jun 2026 retrieval pivot;
    the paid path is now query-aware retrieval = TUL 2.0). The server
    keeps `/v1/tul1` as a deprecated alias for older clients.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

import httpx

logger = logging.getLogger("compresh-mcp.tul2")

DEFAULT_TIMEOUT = 15.0
DEFAULT_API_BASE = "https://api.compre.sh"


@dataclass(slots=True)
class Tul2Result:
    """Successful /v1/tul2 response, unpacked for the MCP server to consume."""

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


class Tul2Error(Exception):
    """Base class for /v1/tul2 client errors."""


class Tul2PaymentRequired(Tul2Error):
    """HTTP 402 — caller's tier / budget does not entitle TUL 2.0 access."""

    def __init__(self, message: str, *, your_tier: str, budget_cents: int):
        super().__init__(message)
        self.your_tier = your_tier
        self.budget_cents = budget_cents


class Tul2NetworkError(Tul2Error):
    """Transport-level failure — caller falls back to local result."""


class Tul2ServerError(Tul2Error):
    """Server returned 5xx — caller falls back to local result."""


async def call_v1_tul2(
    *,
    api_key: str,
    session_id: str,
    messages: list[dict[str, Any]],
    protection_mode: str = "balanced",
    provider_hint: Optional[str] = None,
    model_hint: Optional[str] = None,
    api_base: str = DEFAULT_API_BASE,
    timeout: float = DEFAULT_TIMEOUT,
) -> Tul2Result:
    """Call the Compresh /v1/tul2 server-side enhancement endpoint.

    Raises:
        Tul2PaymentRequired: HTTP 402 — caller's tier / budget rejected.
            The MCP server should surface this in the tool response so
            the user knows why they're not getting enhanced compression.
        Tul2NetworkError: transport failure. Caller falls back to local
            tulbase result silently (degraded mode).
        Tul2ServerError: HTTP 5xx. Caller falls back to local result.

    Network and server errors are NOT fatal — paid users always have a
    valid local fallback because the open-source tulbase core ships in
    the same package.
    """
    url = f"{api_base.rstrip('/')}/v1/tul2"
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
        logger.warning("[/v1/tul2] network error: %s — falling back to local", e)
        raise Tul2NetworkError(f"network: {type(e).__name__}: {e}") from e

    if resp.status_code == 402:
        try:
            data = resp.json()
        except Exception:
            data = {}
        raise Tul2PaymentRequired(
            data.get("error", "TUL 2.0 requires Pro subscription or budget > $0"),
            your_tier=data.get("your_tier", "unknown"),
            budget_cents=data.get("your_budget_cents", 0),
        )

    if 500 <= resp.status_code < 600:
        logger.warning(
            "[/v1/tul2] server %d — falling back to local: %s",
            resp.status_code, resp.text[:200],
        )
        raise Tul2ServerError(f"HTTP {resp.status_code}: {resp.text[:200]}")

    if resp.status_code != 200:
        # 4xx other than 402 — log and treat as fall-back. Don't crash.
        logger.warning(
            "[/v1/tul2] unexpected %d — falling back to local: %s",
            resp.status_code, resp.text[:200],
        )
        raise Tul2NetworkError(f"HTTP {resp.status_code}: {resp.text[:200]}")

    data = resp.json() if resp.content else {}
    return Tul2Result(
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
        version=data.get("version") or "tul2-v?",
    )
