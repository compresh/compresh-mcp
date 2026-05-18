"""Smoke tests for compresh-mcp.

These tests run without network access and without a real Compresh API
key — they exercise the local TUL 1.0 classifiers (Q matrix, epistemic)
and verify the auth/onboarding modules import cleanly. Integration tests
against the live Compresh API live in a separate suite.
"""

from __future__ import annotations


def test_package_importable() -> None:
    import compresh_mcp

    assert compresh_mcp.__version__ == "0.1.0"
    assert compresh_mcp.__license__ == "BUSL-1.1"


def test_tul1_classifiers_importable() -> None:
    """Q matrix + epistemic + semantic store import cleanly."""
    from compresh_mcp.tul1 import (
        EpistemicClassifier,
        QClassification,
        QMatrixClassifier,
        SemanticStore,
    )

    assert QMatrixClassifier is not None
    assert EpistemicClassifier is not None
    assert SemanticStore is not None
    assert QClassification is not None


def test_q_matrix_classifies_simple_sentence() -> None:
    from compresh_mcp.tul1 import QMatrixClassifier

    clf = QMatrixClassifier()
    # Smoke — at minimum, classify_text_pairs should return without error
    # on a trivial input. Don't assert specific Q assignment because
    # heuristics may evolve.
    result = clf.classify_text_pairs("Python is a programming language.")
    assert result is not None


def test_epistemic_classifies_simple_sentence() -> None:
    from compresh_mcp.tul1 import EpistemicClassifier

    clf = EpistemicClassifier()
    # Same smoke approach
    result = clf.classify_text_pairs("I think this might be true.")
    assert result is not None


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
