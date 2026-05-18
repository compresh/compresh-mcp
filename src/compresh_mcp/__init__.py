"""compresh-mcp — MCP server for Compresh paid tier.

Bundles the open-source tulbase compression core (MIT, vendored as
``compresh_mcp.tulbase``) and adds the proprietary TUL 1.0 layer:

    - Q-protective sentence ranking (Q1-Q4 categorization)
    - Epistemic marker classification (VR/HR/CR/UC)
    - Semantic store (cross-turn Q3 dedup)
    - Auth + saving telemetry to Compresh dashboard

The vendored tulbase code is the canonical reference (MIT, © Compresh Ltd 2026,
see ``tulbase/LICENSE``). For the standalone tulbase distribution, see
https://github.com/compresh/tulbase.

For pricing and account management, see https://compre.sh.
"""

from . import tulbase

__version__ = "0.1.0"
__author__ = "Compresh Ltd"
__license__ = "BUSL-1.1"

from .server import main as run_server

__all__ = ["__version__", "run_server", "tulbase"]
