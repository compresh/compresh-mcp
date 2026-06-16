"""Smoke tests for compresh-mcp.

These tests run without network access and without a real Compresh API
key — they verify the package imports cleanly, tulbase vendored
correctly, the /v1/tul2 HTTP client is wired up, and the MCP tool
schemas are present. Integration tests against the live Compresh API
live in a separate suite.
"""

from __future__ import annotations


def test_package_importable() -> None:
    import re

    import compresh_mcp

    # Assert a valid semver, not a hardcoded version: the old "0.2.0" check
    # went stale and failed on every release bump (2026-05-21 fix).
    assert re.fullmatch(r"\d+\.\d+\.\d+", compresh_mcp.__version__), (
        f"unexpected version: {compresh_mcp.__version__!r}"
    )
    assert compresh_mcp.__license__ == "BUSL-1.1"


def test_tulbase_vendored_importable() -> None:
    """tulbase vendored as compresh_mcp.tulbase — core exports present."""
    from compresh_mcp.tulbase import (
        ColdStorage,
        CompressionLog,
        Pipeline,
        Retriever,
        Tier1Summarizer,
        compose_compresh_history,
    )

    assert ColdStorage is not None
    assert CompressionLog is not None
    assert Pipeline is not None
    assert Retriever is not None
    assert Tier1Summarizer is not None
    assert compose_compresh_history is not None


def test_tul2_client_importable() -> None:
    """tul2_client module — HTTP client for /v1/tul2 server endpoint."""
    from compresh_mcp.tul2_client import (
        Tul2NetworkError,
        Tul2PaymentRequired,
        Tul2Result,
        Tul2ServerError,
        call_v1_tul2,
    )

    assert callable(call_v1_tul2)
    assert Tul2Result is not None
    assert issubclass(Tul2PaymentRequired, Exception)
    assert issubclass(Tul2NetworkError, Exception)
    assert issubclass(Tul2ServerError, Exception)


def test_tul1_namespace_removed() -> None:
    """tul1 namespace was archived in 0.2.0 — must NOT be importable."""
    try:
        import compresh_mcp.tul1  # noqa: F401
    except ImportError:
        pass
    else:
        raise AssertionError(
            "compresh_mcp.tul1 namespace must not be importable in 0.2.0+ — "
            "TUL 1.0 layers were moved server-side. See archive/0.1.0-tul1/."
        )


def test_auth_module_no_key_raises() -> None:
    """verify_api_key without env var should raise NoApiKey."""
    import os

    from compresh_mcp.auth import NoApiKey, verify_api_key

    # Temporarily unset to ensure the raise path is exercised
    saved = os.environ.pop("COMPRESH_API_KEY", None)
    try:
        try:
            verify_api_key(api_key="")
        except NoApiKey:
            pass
        else:
            raise AssertionError("expected NoApiKey")
    finally:
        if saved is not None:
            os.environ["COMPRESH_API_KEY"] = saved


def test_server_tool_schemas_present() -> None:
    """All five MCP tools (compress + fetch + list + stats + usage) defined."""
    from compresh_mcp.server import _TOOLS

    names = {t.name for t in _TOOLS}
    assert names == {"compress", "fetch_compressed", "list_compressed", "stats", "usage"}


def test_protection_zone_n_values() -> None:
    from compresh_mcp.server import _PROTECTION_ZONE_N

    assert _PROTECTION_ZONE_N == {
        "off": 0,
        "aggressive": 2,
        "balanced": 4,
        "conservative": 8,
    }
