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

import json
import os
import sys
import textwrap
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Optional

DEFAULT_SIGNUP_URL = os.environ.get(
    "COMPRESH_SIGNUP_URL", "https://compre.sh/signup?source=compresh-mcp"
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


# ---------------------------------------------------------------------------
# Funnel §9 (2026-07-01): free local tulbase + email-only CLI signup + 5-day trial.
# ---------------------------------------------------------------------------

API_BASE = os.environ.get("COMPRESH_API_BASE", "https://api.compre.sh")
CONFIG_DIR = Path.home() / ".compresh"
APIKEY_FILE = CONFIG_DIR / "apikey"          # written by `compresh-mcp signup`; read by auth.get_api_key
ONBOARDED_MARKER = CONFIG_DIR / ".onboarded"  # shown-once guard


def show_first_run_onboarding(*, force: bool = False) -> None:
    """Shown ONCE when compresh-mcp starts WITHOUT a Compresh key. compresh-mcp keeps running
    free local tulbase; this only explains the tulbase (lossy) vs TUL 2.0 (lossless) choice and
    how to unlock. Never exits. Honest per canon: says LexRank is lossy + local-model note."""
    try:
        if not force and ONBOARDED_MARKER.exists():
            return
    except Exception:
        pass

    _eprint(textwrap.dedent(
        """
        Compresh is compressing this conversation locally — free, on your machine. Nothing is sent anywhere.

        You're on TULBASE (open core): extractive LexRank summarization of older turns. Fast and fully
        private — but LOSSY. LexRank keeps the sentences it scores as most important and drops the rest,
        so some detail from older turns can be lost.

        Want lossless memory? TUL 2.0 (tulngin) does query-aware retrieval instead of summarizing: it
        pulls the FULL, unedited older turns relevant to your current question — nothing dropped. Only the
        transcript is sent to compre.sh to compress; your model/provider API key never leaves your machine.

        Two ways forward:
          • Keep TULBASE — free, local, no account. (Nothing to do.)
          • Unlock TUL 2.0:
              compresh-mcp signup <your-email>  → a 5-day taste of TUL 2.0, free (no card);
                                                  verify your inbox for $30 of free credit
              compresh-mcp login --github       → instant $30 (GitHub sign-in counts as verified)
              compresh-mcp login --google       → instant $30 (Google sign-in counts as verified)
            ($30 = we waive our savings-share up to $30 — not cash; no provider key needed.)

        Running a LOCAL / self-hosted model? Mark it in Settings (https://compre.sh/portal) so pricing
        stays $0 — otherwise Compresh assumes a paid cloud model when billing.
        """
    ).strip())

    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        ONBOARDED_MARKER.write_text("shown\n")
    except Exception:
        pass


def cli_signup(email: Optional[str]) -> int:
    """`compresh-mcp signup <email>` — email-only registration (funnel §9). POSTs /auth/register-cli,
    saves the returned Compresh key to ~/.compresh/apikey (picked up by auth.get_api_key), starts the
    5-day TUL 2.0 trial, and tells the user to verify for $30. Returns a process exit code."""
    if not email or "@" not in email:
        _eprint("Usage: compresh-mcp signup <your-email>")
        return 2

    url = f"{API_BASE.rstrip('/')}/auth/register-cli"
    body = json.dumps({"email": email.strip()}).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json", "User-Agent": "compresh-mcp"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        _eprint(f"Registration failed (HTTP {e.code}): {detail}")
        return 1
    except Exception as e:  # network / transport
        _eprint(f"Registration failed: {e}")
        return 1

    key = data.get("api_key")
    if not key:
        _eprint("Registration returned no API key. Try again, or sign up at https://compre.sh/signup")
        return 1

    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        APIKEY_FILE.write_text(key)
        APIKEY_FILE.chmod(0o600)
        saved = f"saved to {APIKEY_FILE}"
    except Exception as e:
        saved = f"could NOT be saved ({e}) — set it manually: export COMPRESH_API_KEY={key}"

    _eprint(textwrap.dedent(
        f"""
        ✓ Registered {email}. Your Compresh key is {saved}.
          • 5 days of TUL 2.0 (query-aware, lossless retrieval) are active now — no card, no provider key.
          • Verify your email (check your inbox) to keep going with $30 of free Compresh credit.
          • Local / self-hosted model? Mark it in Settings: https://compre.sh/portal
        """
    ).strip())
    return 0


_OAUTH_LABELS = {"github": "GitHub", "google": "Google"}
_OAUTH_VERIFY_FALLBACK = {
    "github": "https://github.com/login/device",
    "google": "https://www.google.com/device",
}


def cli_login_oauth(provider: str = "github") -> int:
    """`compresh-mcp login --github|--google` — server-mediated OAuth device flow (funnel §10).
    Starts the flow on api.compre.sh, shows the one-time code + verification URL, polls until
    authorized, and saves the returned Compresh key to ~/.compresh/apikey. The provider token stays
    server-side; the OAuth email is provider-verified, so the account gets instant verified + $30."""
    import time as _time

    label = _OAUTH_LABELS.get(provider, provider)
    base = API_BASE.rstrip("/")

    def _post(path: str, payload: dict):
        req = urllib.request.Request(
            f"{base}{path}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "User-Agent": "compresh-mcp"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b"{}")

    # 1) Start the device flow.
    try:
        _, start = _post(f"/auth/oauth/{provider}/device/start", {})
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        _eprint(f"{label} login unavailable (HTTP {e.code}): {detail}")
        return 1
    except Exception as e:
        _eprint(f"{label} login failed to start: {e}")
        return 1

    poll_token = start.get("poll_token")
    user_code = start.get("user_code")
    verify_uri = start.get("verification_uri") or _OAUTH_VERIFY_FALLBACK.get(provider, "")
    interval = int(start.get("interval") or 5)
    expires_in = int(start.get("expires_in") or 900)
    if not poll_token or not user_code:
        _eprint(f"{label} login start returned an unexpected response.")
        return 1

    _eprint(textwrap.dedent(
        f"""
        To finish signing in with {label}:
          1. Open:  {verify_uri}
          2. Enter code:  {user_code}

        Waiting for authorization… (Ctrl-C to cancel)
        """
    ).strip())
    if os.environ.get("COMPRESH_NO_BROWSER", "").lower() not in ("true", "1", "yes"):
        try:
            webbrowser.open(verify_uri, new=2)
        except Exception:
            pass

    # 2) Poll until authorized (202 = pending), then save the key.
    deadline = _time.time() + min(expires_in, 900)
    while _time.time() < deadline:
        _time.sleep(max(interval, 1))
        try:
            status, data = _post(f"/auth/oauth/{provider}/device/poll", {"poll_token": poll_token})
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            _eprint(f"{label} login failed (HTTP {e.code}): {detail}")
            return 1
        except Exception as e:
            _eprint(f"{label} login network error: {e}")
            return 1

        if status == 202:
            continue  # authorization_pending / slow_down

        key = data.get("api_key")
        email = data.get("email", "")
        if not key:
            _eprint(f"{label} login returned no API key. Try again.")
            return 1
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            APIKEY_FILE.write_text(key)
            APIKEY_FILE.chmod(0o600)
            saved = f"saved to {APIKEY_FILE}"
        except Exception as e:
            saved = f"could NOT be saved ({e}) — set it manually: export COMPRESH_API_KEY={key}"
        _eprint(textwrap.dedent(
            f"""
            ✓ Signed in as {email} via {label}. Your Compresh key is {saved}.
              • TUL 2.0 (query-aware, lossless retrieval) is active now — $30 free Compresh credit applied.
              • Local / self-hosted model? Mark it in Settings: https://compre.sh/portal
            """
        ).strip())
        return 0

    _eprint(f"{label} login timed out before authorization. Run `compresh-mcp login --{provider}` again.")
    return 1


def cli_login_github() -> int:
    """Back-compat alias (pre-0.3.3 imports)."""
    return cli_login_oauth("github")


def show_trial_expired_nudge(*, force: bool = False) -> None:
    """Shown ONCE when /v1/tul2 returns 402 (trial ended / no budget). compresh-mcp keeps working
    on free local TULBASE; this just nudges toward verifying for $30 to keep TUL 2.0."""
    marker = CONFIG_DIR / ".trial_nudged"
    try:
        if not force and marker.exists():
            return
    except Exception:
        pass
    _eprint(textwrap.dedent(
        """
        Your free TUL 2.0 trial has ended — Compresh is back on free local TULBASE (still working,
        just lossy LexRank instead of lossless retrieval). To keep TUL 2.0 going with $30 of free
        Compresh credit: verify your email (https://compre.sh/portal), or sign in once with
        `compresh-mcp login --github` / `--google` — provider sign-in counts as verified.
        """
    ).strip())
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        marker.write_text("shown\n")
    except Exception:
        pass
