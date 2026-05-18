"""MCP server implementation for compresh-mcp (paid tier).

Differences from open-source tulbase-mcp:

    - Tier1Summarizer runs with ``protect_mode="balanced"`` by default,
      with QMatrixClassifier + EpistemicClassifier injected from the
      ``compresh_mcp.tul1`` namespace
    - On startup, validates COMPRESH_API_KEY against the Compresh
      production API
    - Per-session saving telemetry is reported back asynchronously
      (best-effort; local compression never blocks on telemetry)
    - Session state persists in ``~/.compresh/storage/<session_id>/``
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Optional

import mcp.types as mcp_types
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server

# Pipeline + cold storage + compose use tulbase open-source core (PyPI/GitHub).
# The Tier1Summarizer in upstream tulbase is the pre-protect_mode version;
# we ship our own Q-protective summarizer inside compresh_mcp.tul1 and inject
# it via Pipeline(summarizer=...).
from .tulbase import (  # type: ignore[import-not-found]
    ColdStorage,
    CompressionLog,
    Pipeline,
    Retriever,
    compose_compresh_history,
)

from .auth import (
    AuthError,
    AuthNetworkError,
    AuthResult,
    InvalidApiKey,
    NoApiKey,
    get_api_key,
    report_usage,
    verify_api_key,
)
from .onboarding import show_welcome_and_open_signup
from .tul1 import EpistemicClassifier, QMatrixClassifier, Tier1Summarizer

logger = logging.getLogger("compresh-mcp")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_STORAGE_DIR = Path(
    os.environ.get("COMPRESH_STORAGE_DIR", "~/.compresh/storage")
).expanduser()

DEFAULT_PROTECTION_MODE = os.environ.get("COMPRESH_PROTECTION_MODE", "balanced")
DEFAULT_TIER1_PROTECT_MODE = os.environ.get("COMPRESH_TIER1_PROTECT_MODE", "balanced")
ALLOW_OFFLINE = os.environ.get("COMPRESH_ALLOW_OFFLINE", "false").lower() in (
    "true",
    "1",
    "yes",
)

_PROTECTION_ZONE_N = {"off": 0, "aggressive": 2, "balanced": 4, "conservative": 8}


# ---------------------------------------------------------------------------
# Auth state (validated once at startup, cached for session lifetime)
# ---------------------------------------------------------------------------


_auth: Optional[AuthResult] = None


def get_auth() -> AuthResult:
    """Return the validated auth result (set during server startup)."""
    if _auth is None:
        raise RuntimeError("server started without auth — startup bug")
    return _auth


# ---------------------------------------------------------------------------
# Per-session state cache
# ---------------------------------------------------------------------------


class SessionState:
    """Per-session DuckDB + cold storage + Q-protective pipeline."""

    __slots__ = (
        "cold",
        "log",
        "n_compressed_entries",
        "n_turns",
        "pipeline",
        "retriever",
        "saved_chars",
        "session_id",
        "workdir",
    )

    def __init__(self, session_id: str, root: Path):
        self.session_id = session_id
        self.workdir = root / session_id
        self.workdir.mkdir(parents=True, exist_ok=True)

        self.log = CompressionLog(str(self.workdir / "log.duckdb"))
        self.log.ensure_schema()
        self.cold = ColdStorage(str(self.workdir / "cold"))

        # Q-protective balanced — inject classifiers from compresh_mcp.tul1
        # rather than relying on tulbase's auto-init (tulbase distribution
        # does not include Q matrix / epistemic modules).
        self.pipeline = Pipeline(
            log=self.log,
            cold=self.cold,
            enable_q_matrix=False,
            summarizer=Tier1Summarizer(
                protect_mode=DEFAULT_TIER1_PROTECT_MODE,
                q_classifier=QMatrixClassifier(),
                epi_classifier=EpistemicClassifier(),
            ),
        )
        self.retriever = Retriever(log=self.log, cold=self.cold)

        # Running telemetry counters (reported to Compresh API at session
        # close or periodically).
        self.saved_chars = 0
        self.n_compressed_entries = 0
        self.n_turns = 0


_sessions: dict[str, SessionState] = {}


def _get_session(session_id: str) -> SessionState:
    if session_id not in _sessions:
        _sessions[session_id] = SessionState(session_id, DEFAULT_STORAGE_DIR)
        logger.info("created session %s", session_id)
    return _sessions[session_id]


def _normalize_speaker(role: str) -> str:
    role = (role or "user").lower()
    if role in ("assistant", "model", "ai"):
        return "assistant"
    if role in ("system", "tool", "function"):
        return role
    return "user"


# ---------------------------------------------------------------------------
# Tool: compress
# ---------------------------------------------------------------------------


async def tool_compress(
    session_id: str,
    messages: list[dict[str, Any]],
    protection_mode: str = DEFAULT_PROTECTION_MODE,
    provider_hint: Optional[str] = None,
    model_hint: Optional[str] = None,
) -> dict[str, Any]:
    if protection_mode not in _PROTECTION_ZONE_N:
        return {
            "ok": False,
            "error": f"protection_mode must be one of {list(_PROTECTION_ZONE_N)}",
        }

    state = _get_session(session_id)
    n_zone = _PROTECTION_ZONE_N[protection_mode]

    if len(messages) <= n_zone + 1:
        # Still report passthrough telemetry — dogfood pattern needs every
        # turn visible in the dashboard, not just turns that triggered
        # compression. Saving=0, no compressed entries, but the row marks
        # "Compresh was consulted at this turn".
        state.n_turns = max(state.n_turns, len(messages))
        asyncio.create_task(
            _report_usage_background(
                session_id=session_id,
                saved_input_tokens=0,
                saved_chars=0,
                n_turns=state.n_turns,
                n_compressed_entries=0,
                provider_hint=provider_hint,
                model_hint=model_hint,
            )
        )
        return {
            "ok": True,
            "applied": False,
            "reason": "conversation_within_protection_zone",
            "optimized_messages": messages,
            "compresh_md": "",
            "raw_tail": messages,
            "n_compressed_turns": 0,
            "n_compressed_entries": 0,
            "n_total": len(messages),
            "saving_chars": 0,
            "session_id": session_id,
            "protection_mode": protection_mode,
            "protection_zone_n": n_zone,
            "tools_hint": ["fetch_compressed", "list_compressed"],
            "tier": (get_auth().tier or "unknown"),
        }

    turn_boxes = []
    for i, m in enumerate(messages):
        content = m.get("content", "")
        if not isinstance(content, str):
            content = json.dumps(content)
        try:
            pr = state.pipeline.run(
                content,
                session_id=session_id,
                turn_idx=i,
                speaker=_normalize_speaker(m.get("role", "user")),
            )
            turn_boxes.append(pr.turn_box)
        except Exception as e:
            logger.warning("pipeline.run failed at turn %d: %s", i, e)
            return {
                "ok": False,
                "error": f"pipeline error at turn {i}: {type(e).__name__}",
                "optimized_messages": messages,
                "n_total": len(messages),
                "session_id": session_id,
            }

    composed = compose_compresh_history(
        messages, turn_boxes, upto_idx=len(messages) - 1, mode=protection_mode,
    )

    # Symmetric saving comparison:
    # Without Compresh, the upstream would see ALL messages verbatim.
    # With Compresh, it sees system(compresh_md) + raw_tail.
    # Both sides include the current user, so raw_chars iterates over all
    # messages (not messages[:-1] which underestimates by excluding the
    # trailing user from raw but keeping it in raw_tail).
    raw_chars = sum(
        len(m.get("content") or "")
        for m in messages
        if isinstance(m.get("content"), str)
    )
    optimized_chars = (
        len(composed.compresh_md or "")
        + sum(
            len(m.get("content") or "")
            for m in composed.raw_tail
            if isinstance(m.get("content"), str)
        )
    )
    # Allow negative saving — honest reporting. Short/sparse conversations
    # incur TurnBox overhead that exceeds the content shaved (~200 char
    # per turn header). The dashboard should reflect this so users can see
    # the break-even point in their own usage patterns.
    saving_chars = raw_chars - optimized_chars
    n_compressed_entries = sum(
        len(b.compressed_refs) for b in turn_boxes[: composed.n_compressed]
    )

    state.saved_chars += saving_chars
    state.n_compressed_entries += n_compressed_entries
    state.n_turns = max(state.n_turns, len(messages))

    optimized_messages: list[dict[str, Any]] = []
    if composed.compresh_md:
        optimized_messages.append({
            "role": "system",
            "content": (
                "Below is a compressed memory of older turns "
                "(the most recent turns follow as raw messages):\n\n"
                + composed.compresh_md
            ),
        })
    optimized_messages.extend(composed.raw_tail)

    # Fire-and-forget telemetry — local compression has already succeeded.
    # Approximation: saving_chars / 4 = saved_input_tokens (heuristic).
    asyncio.create_task(
        _report_usage_background(
            session_id=session_id,
            saved_input_tokens=saving_chars // 4,
            saved_chars=saving_chars,
            n_turns=state.n_turns,
            n_compressed_entries=state.n_compressed_entries,
            provider_hint=provider_hint,
            model_hint=model_hint,
        )
    )

    return {
        "ok": True,
        "applied": True,
        "compresh": True,
        "tulbase": True,
        "optimized_messages": optimized_messages,
        "compresh_md": composed.compresh_md or "",
        "raw_tail": list(composed.raw_tail),
        "n_compressed_turns": composed.n_compressed,
        "n_compressed_entries": n_compressed_entries,
        "n_total": len(messages),
        "saving_chars": saving_chars,
        # Debug breakdown so we can see exactly which side overweighs.
        "_debug_raw_chars": raw_chars,
        "_debug_optimized_chars": optimized_chars,
        "_debug_compresh_md_len": len(composed.compresh_md or ""),
        "_debug_raw_tail_chars": sum(
            len(m.get("content") or "")
            for m in composed.raw_tail
            if isinstance(m.get("content"), str)
        ),
        "session_id": session_id,
        "protection_mode": protection_mode,
        "protection_zone_n": n_zone,
        "protect_mode_active": DEFAULT_TIER1_PROTECT_MODE,
        "q_classifier_enabled": True,
        "epi_classifier_enabled": True,
        "tools_hint": ["fetch_compressed", "list_compressed"],
        "tier": (get_auth().tier or "unknown"),
    }


async def _report_usage_background(**kwargs: Any) -> None:
    """Run usage telemetry in a worker thread to avoid blocking the MCP loop."""
    auth = get_auth()
    await asyncio.to_thread(report_usage, auth.api_key, **kwargs)


# ---------------------------------------------------------------------------
# Tool: fetch_compressed / list_compressed / stats / usage
# ---------------------------------------------------------------------------


async def tool_fetch_compressed(
    session_id: str, entry_id: str, max_tokens: int = 2000,
) -> dict[str, Any]:
    state = _get_session(session_id)
    return state.retriever.fetch(entry_id, max_tokens=max_tokens).to_tool_response()


async def tool_list_compressed(
    session_id: str,
    turn_min: Optional[int] = None,
    turn_max: Optional[int] = None,
    modality: Optional[str] = None,
    limit: int = 100,
) -> dict[str, Any]:
    state = _get_session(session_id)
    limit = max(1, min(limit, 1000))

    # Use tulbase's public CompressionLog.list_by_session API rather than
    # poking at the internal ._conn attribute — keeps us compatible with
    # whatever DuckDB binding tulbase ships.
    entries_obj = state.log.list_by_session(
        session_id,
        turn_min=turn_min,
        turn_max=turn_max,
        modality=modality,
        limit=limit,
    )

    entries = [
        {
            "id": e.id,
            "turn_idx": e.turn_idx,
            "modality": e.modality,
            "summary_short": getattr(e, "summary_short", None),
            "chars": getattr(e, "n_chars", None),
            "retrievable": getattr(e, "retrievable", True),
            "pii_filtered": getattr(e, "pii_filtered", False),
        }
        for e in entries_obj
    ]

    return {
        "ok": True,
        "entries": entries,
        "total": len(entries),
        "session_id": session_id,
        "limit_reached": len(entries) == limit,
    }


async def tool_stats(session_id: str) -> dict[str, Any]:
    state = _get_session(session_id)
    return {
        "ok": True,
        "session_id": session_id,
        "n_turns": state.n_turns,
        "n_compressed_entries": state.n_compressed_entries,
        "saved_chars": state.saved_chars,
        "storage_path": str(state.workdir),
        "protect_mode_active": DEFAULT_TIER1_PROTECT_MODE,
        "q_classifier_enabled": True,
        "epi_classifier_enabled": True,
    }


async def tool_usage(_session_id: str) -> dict[str, Any]:
    """Return current Compresh account budget/credit snapshot."""
    auth = get_auth()
    return {
        "ok": True,
        "email": auth.email,
        "tier": auth.tier,
        "free_credit_remaining": auth.free_credit_remaining,
        "budget_remaining": auth.budget_remaining,
    }


# ---------------------------------------------------------------------------
# MCP tool schemas
# ---------------------------------------------------------------------------

_TOOLS: list[mcp_types.Tool] = [
    mcp_types.Tool(
        name="compress",
        description=(
            "Compress a conversation message list using Compresh (Q-protective + "
            "epistemic-aware). Elides code blocks, terminal output, JSON dumps, "
            "and stack traces to cold storage with retrievable IDs. Ranks "
            "fact-bearing sentences for preservation when compression capacity "
            "is constrained. Preserves the last N messages verbatim (Protection Zone)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "messages": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "role": {"type": "string"},
                            "content": {"type": "string"},
                        },
                        "required": ["role", "content"],
                    },
                },
                "protection_mode": {
                    "type": "string",
                    "enum": ["off", "aggressive", "balanced", "conservative"],
                    "default": "balanced",
                },
                "provider_hint": {
                    "type": "string",
                    "description": (
                        "Optional provider family for tier-B pricing detection "
                        "(anthropic | openai | google | mistral | meta | deepseek | xai)."
                    ),
                },
                "model_hint": {
                    "type": "string",
                    "description": "Optional specific model name for tier-A pricing.",
                },
            },
            "required": ["session_id", "messages"],
        },
    ),
    mcp_types.Tool(
        name="fetch_compressed",
        description=(
            "Retrieve the original content of a compressed entry by ID. If the "
            "entry is not retrievable, say so — do not fabricate."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "entry_id": {"type": "string"},
                "max_tokens": {
                    "type": "integer",
                    "default": 2000,
                    "minimum": 1,
                    "maximum": 32000,
                },
            },
            "required": ["session_id", "entry_id"],
        },
    ),
    mcp_types.Tool(
        name="list_compressed",
        description="List compressed entries in the current session, optionally filtered.",
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "turn_min": {"type": "integer"},
                "turn_max": {"type": "integer"},
                "modality": {
                    "type": "string",
                    "enum": ["code", "terminal_output", "json_dump", "stack_trace"],
                },
                "limit": {"type": "integer", "default": 100, "minimum": 1, "maximum": 1000},
            },
            "required": ["session_id"],
        },
    ),
    mcp_types.Tool(
        name="stats",
        description="Session-level compression statistics.",
        inputSchema={
            "type": "object",
            "properties": {"session_id": {"type": "string"}},
            "required": ["session_id"],
        },
    ),
    mcp_types.Tool(
        name="usage",
        description="Current Compresh account snapshot (tier, budget, free credit).",
        inputSchema={
            "type": "object",
            "properties": {"session_id": {"type": "string"}},
            "required": ["session_id"],
        },
    ),
]


# ---------------------------------------------------------------------------
# MCP wiring
# ---------------------------------------------------------------------------


app: Server = Server("compresh-mcp")


@app.list_tools()
async def _list_tools() -> list[mcp_types.Tool]:
    return _TOOLS


@app.call_tool()
async def _call_tool(name: str, arguments: dict[str, Any]) -> list[mcp_types.TextContent]:
    try:
        if name == "compress":
            result = await tool_compress(
                session_id=arguments["session_id"],
                messages=arguments["messages"],
                protection_mode=arguments.get("protection_mode", DEFAULT_PROTECTION_MODE),
                provider_hint=arguments.get("provider_hint"),
                model_hint=arguments.get("model_hint"),
            )
        elif name == "fetch_compressed":
            result = await tool_fetch_compressed(
                session_id=arguments["session_id"],
                entry_id=arguments["entry_id"],
                max_tokens=arguments.get("max_tokens", 2000),
            )
        elif name == "list_compressed":
            result = await tool_list_compressed(
                session_id=arguments["session_id"],
                turn_min=arguments.get("turn_min"),
                turn_max=arguments.get("turn_max"),
                modality=arguments.get("modality"),
                limit=arguments.get("limit", 100),
            )
        elif name == "stats":
            result = await tool_stats(session_id=arguments["session_id"])
        elif name == "usage":
            result = await tool_usage(_session_id=arguments["session_id"])
        else:
            result = {"ok": False, "error": f"unknown tool: {name}"}
    except KeyError as e:
        result = {"ok": False, "error": f"missing required argument: {e}"}
    except Exception as e:
        logger.exception("tool %s failed", name)
        result = {"ok": False, "error": f"{type(e).__name__}: {e}"}

    return [mcp_types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------


def _bootstrap_auth() -> AuthResult:
    """Validate COMPRESH_API_KEY or trigger onboarding flow."""
    global _auth
    try:
        result = verify_api_key()
        _auth = result
        logger.info(
            "auth ok — email=%s tier=%s credit=%s budget=%s",
            result.email, result.tier,
            result.free_credit_remaining, result.budget_remaining,
        )
        return result
    except NoApiKey:
        show_welcome_and_open_signup()
        sys.exit(2)
    except InvalidApiKey as e:
        print(f"\nCompresh API key was rejected: {e}", file=sys.stderr)
        print("Check your COMPRESH_API_KEY value, or visit "
              "https://compre.sh/portal to regenerate.", file=sys.stderr)
        sys.exit(3)
    except AuthNetworkError as e:
        if ALLOW_OFFLINE:
            api_key = get_api_key() or ""
            _auth = AuthResult(ok=True, api_key=api_key, tier="offline")
            logger.warning("offline mode (auth network error: %s)", e)
            return _auth
        print(f"\nCould not reach Compresh server: {e}", file=sys.stderr)
        print("To run anyway with cached compression, set "
              "COMPRESH_ALLOW_OFFLINE=true.", file=sys.stderr)
        sys.exit(4)


async def serve() -> None:
    DEFAULT_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    auth = get_auth()
    logger.info(
        "compresh-mcp starting (storage=%s tier=%s)",
        DEFAULT_STORAGE_DIR, auth.tier,
    )

    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream, write_stream,
            InitializationOptions(
                server_name="compresh-mcp",
                server_version="0.1.0",
                capabilities=app.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    _bootstrap_auth()
    asyncio.run(serve())


if __name__ == "__main__":
    main()
