"""MCP server implementation for compresh-mcp (paid tier).

Differences from open-source tulbase-mcp:

    - On startup, validates COMPRESH_API_KEY against the Compresh
      production API
    - Per turn, runs the open-source tulbase compression locally for
      cold-storage + fetch_compressed support, AND calls the paid
      ``/v1/tul1`` server endpoint for TUL 1.0 enhancement (Q-protective
      ranking + epistemic markers). On network/server failure, falls
      back to the local result silently.
    - Per-session saving telemetry is reported back asynchronously
      (best-effort; local compression never blocks on telemetry)
    - Session state persists in ``~/.compresh/storage/<session_id>/``

Architecture change vs 0.1.0: TUL 1.0 layers moved server-side.
See ``archive/0.1.0-tul1/README.md`` and CHANGELOG for context.
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

# tulbase (MIT, vendored as ``compresh_mcp.tulbase``) is the open-source
# compression core — LexRank summarization, Protection Zone, cold storage,
# modality elision. It runs locally on every turn for cold-storage support.
from .tulbase import (  # type: ignore[import-not-found]
    ColdStorage,
    CompressionLog,
    Pipeline,
    Retriever,
    Tier1Summarizer,
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
from .tul1_client import (
    Tul1NetworkError,
    Tul1PaymentRequired,
    Tul1ServerError,
    call_v1_tul1,
)

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

        # Local pipeline runs the open-source tulbase core only — no Q-protective
        # ranking, no epistemic markers, no semantic store. Those layers run on
        # the Compresh server (see ``tul1_client.call_v1_tul1``). Local pipeline
        # is still needed for cold storage + fetch_compressed retrieval support.
        self.pipeline = Pipeline(
            log=self.log,
            cold=self.cold,
            enable_q_matrix=False,
            summarizer=Tier1Summarizer(),
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

    local_composed = compose_compresh_history(
        messages, turn_boxes, upto_idx=len(messages) - 1, mode=protection_mode,
    )

    # ── Server-side TUL 1.0 overlay (paid tier) ────────────────────
    # compresh-mcp >=0.2.0 always has a valid API key (startup gate).
    # Call /v1/tul1 for the enhanced compressed view; on any failure
    # silently fall back to the local result. This is degraded mode —
    # the user still sees compression, just without TUL 1.0 enhancement.
    auth = get_auth()
    tul1_used = False
    tul1_payment_required = False
    tul1_error: Optional[str] = None
    server_compresh_md: Optional[str] = None
    server_raw_tail: Optional[list] = None
    server_n_compressed_turns: Optional[int] = None
    if auth.api_key and auth.tier != "offline":
        try:
            tul1_result = await call_v1_tul1(
                api_key=auth.api_key,
                session_id=session_id,
                messages=messages,
                protection_mode=protection_mode,
                provider_hint=provider_hint,
                model_hint=model_hint,
            )
            tul1_used = True
            server_compresh_md = tul1_result.compresh_md
            server_raw_tail = tul1_result.raw_tail
            server_n_compressed_turns = tul1_result.n_compressed_turns
        except Tul1PaymentRequired as e:
            tul1_payment_required = True
            tul1_error = f"payment-required: {e}"
            logger.info(
                "/v1/tul1 payment required (your_tier=%s, budget_cents=%d) — using local result",
                e.your_tier, e.budget_cents,
            )
        except (Tul1NetworkError, Tul1ServerError) as e:
            tul1_error = str(e)
            logger.warning("/v1/tul1 unavailable — using local result: %s", e)
        except Exception as e:
            tul1_error = f"unexpected: {type(e).__name__}: {e}"
            logger.warning("/v1/tul1 unexpected error — using local result: %s", e)

    # Resolved compressed view — server overrides local on success.
    compresh_md = server_compresh_md if tul1_used else (local_composed.compresh_md or "")
    raw_tail = server_raw_tail if tul1_used and server_raw_tail else list(local_composed.raw_tail)
    n_compressed_turns = (
        server_n_compressed_turns if tul1_used and server_n_compressed_turns is not None
        else local_composed.n_compressed
    )

    # ── Saving math (honest reporting) ─────────────────────────────
    # Without Compresh, upstream would see ALL messages verbatim.
    # With Compresh, it sees system(compresh_md) + raw_tail.
    raw_chars = sum(
        len(m.get("content") or "")
        for m in messages
        if isinstance(m.get("content"), str)
    )
    optimized_chars = (
        len(compresh_md or "")
        + sum(
            len(m.get("content") or "")
            for m in (raw_tail or [])
            if isinstance(m.get("content"), str)
        )
    )
    saving_chars = raw_chars - optimized_chars
    n_compressed_entries = sum(
        len(b.compressed_refs) for b in turn_boxes[: local_composed.n_compressed]
    )

    state.saved_chars += saving_chars
    state.n_compressed_entries += n_compressed_entries
    state.n_turns = max(state.n_turns, len(messages))

    optimized_messages: list[dict[str, Any]] = []
    if compresh_md:
        optimized_messages.append({
            "role": "system",
            "content": (
                "Below is a compressed memory of older turns "
                "(the most recent turns follow as raw messages):\n\n"
                + compresh_md
            ),
        })
    optimized_messages.extend(raw_tail or [])

    # Fire-and-forget telemetry — local compression has already succeeded.
    # When tul1_used, /v1/tul1 already wrote a row server-side; we still
    # call report_usage so /v1/me/usage stays consistent across both
    # source labels. Future: dedupe at the server side.
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
        "tul1_server_used": tul1_used,
        "tul1_payment_required": tul1_payment_required,
        "tul1_error": tul1_error,
        "optimized_messages": optimized_messages,
        "compresh_md": compresh_md or "",
        "raw_tail": list(raw_tail or []),
        "n_compressed_turns": n_compressed_turns,
        "n_compressed_entries": n_compressed_entries,
        "n_total": len(messages),
        "saving_chars": saving_chars,
        "_debug_raw_chars": raw_chars,
        "_debug_optimized_chars": optimized_chars,
        "_debug_compresh_md_len": len(compresh_md or ""),
        "_debug_raw_tail_chars": sum(
            len(m.get("content") or "")
            for m in (raw_tail or [])
            if isinstance(m.get("content"), str)
        ),
        "session_id": session_id,
        "protection_mode": protection_mode,
        "protection_zone_n": n_zone,
        "tools_hint": ["fetch_compressed", "list_compressed"],
        "tier": (auth.tier or "unknown"),
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
                server_version="0.2.2",
                capabilities=app.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


def _close_all_sessions() -> None:
    """Cleanly close all per-session DuckDB connections.

    Without this, a crash or abrupt SIGTERM leaves ``log.duckdb.wal`` files
    behind. The next process tries to recover the WAL and can fail with a
    ``duckdb.IOException``, breaking the next compress call. Run on
    ``atexit`` + ``SIGTERM``/``SIGINT`` so the host (MCP gateway, Claude
    Desktop, etc.) restarting our subprocess doesn't poison the storage.
    """
    for sid, state in list(_sessions.items()):
        try:
            # CompressionLog wraps a duckdb.connection — close the underlying
            # connection if exposed.
            conn = getattr(state.log, "_conn", None)
            if conn is not None:
                conn.close()
        except Exception:
            pass
    _sessions.clear()


def _install_lifecycle_handlers() -> None:
    """Register atexit + SIGTERM/SIGINT handlers for clean shutdown."""
    import atexit
    import signal

    atexit.register(_close_all_sessions)

    def _signal_handler(signum: int, _frame: Any) -> None:
        _close_all_sessions()
        # Re-raise the default behaviour for this signal so the host sees
        # the expected exit code.
        sys.exit(128 + signum)

    for sig_name in ("SIGTERM", "SIGINT", "SIGHUP"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _signal_handler)
        except (ValueError, OSError):
            # Not running in the main thread, or platform doesn't allow.
            pass


def main() -> None:
    verbose = "--verbose" in sys.argv or os.environ.get(
        "COMPRESH_VERBOSE", ""
    ).lower() in ("true", "1", "yes")
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    _install_lifecycle_handlers()
    _bootstrap_auth()
    asyncio.run(serve())


if __name__ == "__main__":
    main()
