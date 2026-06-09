"""compresh-mcp — MCP server for Compresh paid tier.

Bundles the open-source tulbase compression core (MIT, vendored as
``compresh_mcp.tulbase``) and calls the Compresh server for the
proprietary TUL 1.0 layer:

    - Q-protective sentence ranking (Q1-Q4 categorization)
    - Epistemic marker classification (VR/HR/CR/UC)
    - Semantic store (cross-turn Q3 dedup)

TUL 1.0 layers run server-side via ``/v1/tul1`` and require a valid
Compresh API key. Local compression (LexRank + Protection Zone +
modality elision) always runs from the vendored tulbase core — when
the server is unreachable, compresh-mcp degrades gracefully to local
tulbase results.

The vendored tulbase code is the canonical reference (MIT, © Compresh Ltd 2026,
see ``tulbase/LICENSE``). For the standalone tulbase distribution, see
https://github.com/compresh/tulbase.

For pricing and account management, see https://compre.sh.

Architecture history:

    0.1.0 (2026-05-18 13:00 UTC) — shipped TUL 1.0 classifiers locally
        in ``compresh_mcp.tul1`` namespace (architectural mistake — paid
        features leaked into the local install). Yanked.

    0.2.0 (2026-05-18 ~15:30 UTC) — TUL 1.0 moved server-side behind
        ``/v1/tul1``. Local pipeline keeps tulbase only.

    0.2.1 (2026-05-19 evening) — UX cleanup for MCP host integration:
        log level WARNING by default (INFO behind ``--verbose`` /
        ``COMPRESH_VERBOSE=1``), atexit + SIGTERM/SIGINT handlers for
        clean DuckDB shutdown (prevents ``log.duckdb.wal`` corruption
        on host restart), onboarding URL fix
        (``api.compre.sh`` → ``compre.sh``).
"""

from . import tulbase

__version__ = "0.2.5"
__author__ = "Compresh Ltd"
__license__ = "BUSL-1.1"

from .server import main as run_server

__all__ = ["__version__", "run_server", "tulbase"]
