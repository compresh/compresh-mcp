# compresh-mcp

**MCP server for Compresh** — production-grade context compression for LLM agent conversations.

> Compresh adds **query-aware retrieval (TUL 2.0)** on top of the open-source
> [`tulbase`](https://github.com/compresh/tulbase) compression core. Instead of
> summarizing older turns, it retrieves the turns relevant to the current
> question — lossless and role-tagged — from the full conversation history.
> This is the **paid tier** distribution, and it works free without an account
> (local tulbase compression).

## Architecture (0.3.0+)

- **Local**: bundled `tulbase` (MIT, vendored as `compresh_mcp.tulbase`) runs on every turn for fast compression + cold-storage support (`fetch_compressed` / `list_compressed` MCP tools). Works with **no API key and no account** — fully local, free.
- **Server**: the **TUL 2.0** paid layer (query-aware retrieval over full history, role-preserving render) runs on Compresh infrastructure via the `/v1/tul2` endpoint, gated by your Compresh API key + tier.
- **Degraded mode**: if `/v1/tul2` is unreachable (or your trial/credit runs out), compresh-mcp transparently falls back to the local tulbase result so compression never blocks.

The paid endpoint was the legacy `/v1/tul1` through 0.2.x; 0.3.0 calls the
canonical `/v1/tul2` (the TUL 1.0 Q-matrix layer was retired in the 15 Jun
2026 retrieval pivot). The server keeps `/v1/tul1` as a deprecated alias, so
older clients keep working. Previously (0.1.0, yanked), paid classifiers
shipped inside the client package and leaked paid features locally — install
0.3.0 or later. See [CHANGELOG.md](./CHANGELOG.md).

## What's the difference vs `tulbase-mcp`?

Both ship the same open-source compression core (cleanup, TurnBox, modality
elision to cold storage, Protection Zone, LexRank summarization). `tulbase-mcp`
is currently GitHub-only (not yet on PyPI). The difference is what happens on
top of the shared core:

| Feature | `tulbase-mcp` (free, open-source) | `compresh-mcp` (paid) |
|---|---|---|
| Local tulbase compression (LexRank, modality elision, cold storage + fetch, Protection Zone) | ✅ | ✅ |
| **TUL 2.0 query-aware retrieval** (lossless — full relevant turns instead of summaries, role-preserving) | ❌ | ✅ (paid models, server-side) |
| Savings telemetry + Compresh dashboard | ❌ | ✅ |
| `usage` tool (budget, credit, savings metrics) | ❌ | ✅ |
| Account CLI (`signup`, `login --github`) | ❌ | ✅ |
| Multi-device sync (planned) | ❌ | ✅ |

**Why retrieval instead of summarization?** Summaries are lossy by
construction. In our T-bench (976-probe episodic benchmark, run registry
published), TUL 2.0 retrieval **matched or beat sending the full raw
history** while using **~99% fewer input tokens** on long conversations —
and on weaker models it improved answer quality by up to **+22 points**,
where raw history either diluted the answer or simply didn't fit the
context window.

## Pricing

| Plan | Period | Saving-share |
|---|---|---|
| **Starter** (free + budget loaded) | pay-as-you-go | **30%** |
| **Pro Quarterly** ($18) | 3 mo | **20%** |
| **Pro Semi-Annual** ($33) | 6 mo | **16%** |
| **Pro Annual** ($60) | 1 yr | **12%** |
| No key / anonymous / free / local LLM | — | 0% (free, local tulbase) |

- **5-day free TUL 2.0 trial** on every new account (no card, no commitment). When it ends, you fall back to free local tulbase — compression never stops.
- **$30 free credit** for every **verified** account: verify your email, or sign in with GitHub (provider-verified — instant). $30 = we waive our savings-share up to $30; it is not cash and no card is required.
- **$10 minimum top-up**, charged $7.50 with a permanent 25% top-up discount.

Saving-share is the cut Compresh takes on the **savings** measured against the user's chosen model. The base value comes from the actual model price (when known) or the provider family's cheapest model; anonymous / free-model usage falls back to a flat $0.20 / 1M saved input tokens.

Running a **local / self-hosted model**? Mark it in Settings ([compre.sh/portal](https://compre.sh/portal)) — local models are free, so your savings-share stays $0.

See [compre.sh/pricing](https://compre.sh/pricing) for the canonical pricing page.

## Installation

```bash
pip install compresh-mcp
```

**No account needed to start** — on first run compresh-mcp compresses locally
with the free tulbase core and prints a one-time note about the upgrade path.

To unlock TUL 2.0:

```bash
compresh-mcp signup you@example.com   # email-only signup — starts your 5-day trial
compresh-mcp login --github           # or sign in with GitHub (instant $30, no email verify)
```

Both write your key to `~/.compresh/apikey`, where the server picks it up
automatically. You can also set `COMPRESH_API_KEY` explicitly.

## MCP client configuration

### Claude Code (`~/.claude/mcp.json`)

```json
{
  "mcpServers": {
    "compresh": {
      "command": "compresh-mcp",
      "env": {
        "COMPRESH_API_KEY": "sk-comp_...",
        "COMPRESH_API_BASE": "https://api.compre.sh"
      }
    }
  }
}
```

(Omit `COMPRESH_API_KEY` to run in free local mode.)

### Cursor (`~/.cursor/mcp.json`)

Same structure as Claude Code.

### Cowork

Cowork → Settings → Tools → MCP servers → Add:
- Command: `compresh-mcp`
- Environment: `COMPRESH_API_KEY=sk-comp_...` (optional)

## Tools exposed

Same four tools as `tulbase-mcp`, with enhanced behavior:

- `compress` — local tulbase compression on every turn (`protection_mode="balanced"`); on paid models with a valid key, upgraded server-side to TUL 2.0 query-aware retrieval
- `fetch_compressed`, `list_compressed`, `stats` — same interface

Plus paid-tier extras:

- `usage` — current cycle budget, free credit balance, savings metrics

## License

Business Source License 1.1 — see [LICENSE](./LICENSE). Production use
permitted with valid Compresh API key. License automatically converts
to MIT after 4 years (Year 2030).

## Patents

Q-protective sentence ranking + Protection Zone are covered by
**TR-TPMK patent application 2026/007305** (Compresh Ltd, May 2026).
A valid Compresh subscription grants implementation license.

## Status

`v0.3.2` — active development. APIs may change before `v1.0`. Issues and
pull requests welcome.

## Links

- [compre.sh](https://compre.sh) — product site
- [Documentation](https://compre.sh/docs) — full reference
- [GitHub](https://github.com/compresh/compresh-mcp) — source
- [Issues](https://github.com/compresh/compresh-mcp/issues) — bug reports
- [tulbase (open core, MIT)](https://github.com/compresh/tulbase) — standalone
