"""Onboarding flow for new compresh-mcp users.

When the user starts the server without a valid COMPRESH_API_KEY, this
module:

    1. Prints a friendly help message to stderr (so MCP stdio is not
       polluted).
    2. Opens the signup page in the default browser.
    3. Exits with a non-zero status so the MCP client surfaces the
       failure to the user.

Existing users (anyone with an API key already in hand) skip this flow
by setting COMPRESH_API_KEY in their MCP client's environment config.
"""

from __future__ import annotations

import os
import sys
import textwrap
import webbrowser

DEFAULT_SIGNUP_URL = os.environ.get(
    "COMPRESH_SIGNUP_URL", "https://api.compre.sh/signup?source=compresh-mcp"
)
DEFAULT_DOCS_URL = os.environ.get(
    "COMPRESH_DOCS_URL", "https://compre.sh/docs/mcp"
)


def _eprint(msg: str) -> None:
    """Print to stderr (stdin/stdout reserved for MCP stdio transport)."""
    print(msg, file=sys.stderr, flush=True)


def show_welcome_and_open_signup(
    *,
    signup_url: str = DEFAULT_SIGNUP_URL,
    docs_url: str = DEFAULT_DOCS_URL,
    open_browser: bool = True,
) -> None:
    """Display onboarding help and optionally launch the browser.

    Always prints help text; ``open_browser`` controls whether to invoke
    ``webbrowser.open``. Set ``COMPRESH_NO_BROWSER=true`` to disable
    auto-launch (useful in headless CI or remote terminals).
    """
    no_browser = os.environ.get("COMPRESH_NO_BROWSER", "").lower() in (
        "true",
        "1",
        "yes",
    )

    msg = textwrap.dedent(
        f"""
        ╭─────────────────────────────────────────────────────────────╮
        │                                                             │
        │  compresh-mcp needs a Compresh API key to run.              │
        │                                                             │
        │  If you have an account:                                    │
        │    Set COMPRESH_API_KEY in your MCP client config:          │
        │                                                             │
        │      {{                                                     │
        │        "mcpServers": {{                                     │
        │          "compresh": {{                                     │
        │            "command": "compresh-mcp",                       │
        │            "env": {{ "COMPRESH_API_KEY": "sk-comp_..." }}   │
        │          }}                                                 │
        │        }}                                                   │
        │      }}                                                     │
        │                                                             │
        │  New users — sign up + add $10 budget:                      │
        │    {signup_url:<53s} │
        │                                                             │
        │    Every new signup gets $30 free credit (90-day expiry).   │
        │    Welcome email + API key arrive automatically.            │
        │                                                             │
        │  Docs:                                                      │
        │    {docs_url:<53s} │
        │                                                             │
        ╰─────────────────────────────────────────────────────────────╯
        """
    ).strip()

    _eprint(msg)

    if open_browser and not no_browser:
        try:
            opened = webbrowser.open(signup_url, new=2)
            if opened:
                _eprint(f"\n→ Opened {signup_url} in your default browser.")
            else:
                _eprint(f"\n→ Could not auto-open browser. Visit {signup_url} manually.")
        except Exception as e:
            _eprint(f"\n→ Browser launch failed ({e!r}). Visit {signup_url} manually.")
