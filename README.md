# compresh-mcp

**MCP server for Compresh** — production-grade context compression for LLM agent conversations.

> Compresh adds Q-protective ranking, epistemic marker classification, and
> depth-aware adaptation on top of the open-source [`tulbase`](https://github.com/compresh/tulbase)
> compression core. This is the **paid tier** distribution.

## Architecture (0.2.0+)

- **Local**: bundled `tulbase` (MIT, vendored as `compresh_mcp.tulbase`) runs on every turn for fast compression + cold-storage support (`fetch_compressed` / `list_compressed` MCP tools).
- **Server**: TUL 1.0 layers (Q-protective ranking, epistemic markers, semantic store) run on Compresh infrastructure via the `/v1/tul1` endpoint, gated by your Compresh API key + tier.
- **Degraded mode**: if `/v1/tul1` is unreachable, compresh-mcp transparently falls back to the local tulbase result so compression never blocks.

Previously (0.1.0, yanked), TUL 1.0 classifiers shipped inside the client
package. That distribution leaked paid features into the local install
and is no longer recommended — install 0.2.0 or later. See
[CHANGELOG.md](./CHANGELOG.md) for the migration note.

## What's the difference vs `tulbase-mcp`?

| Feature | `tulbase-mcp` (free, open-source) | `compresh-mcp` (paid) |
|---|---|---|
| Base LexRank summarization | ✅ | ✅ |
| Modality elision (code, terminal, JSON, stack traces) | ✅ | ✅ |
| Cold storage + fetch_compressed | ✅ | ✅ |
| Protection Zone (Claim 1e) | ✅ | ✅ |
| **Q-protective sentence ranking** (Q1–Q4 categorization) | ❌ | ✅ |
| **Epistemic markers** (VR/HR/CR/UC) | ❌ | ✅ |
| **Semantic store** (cross-turn Q3 dedup) | ❌ | ✅ |
| Saving telemetry to Compresh dashboard | ❌ | ✅ |
| Multi-device sync (planned) | ❌ | ✅ |

In Compresh's bench (Compresh-bench v1, 600-turn multi-model), Q-protective
ranking adds **5–12 percentage points** of equivalence preservation vs
base LexRank at the same token savings — Pareto improvement.

## Pricing

| Plan | Period | Saving-share |
|---|---|---|
| **Starter** (free + budget loaded) | pay-as-you-go | **30%** |
| **Pro Quarterly** ($18) | 3 mo | **20%** |
| **Pro Semi-Annual** ($33) | 6 mo | **16%** |
| **Pro Annual** ($60) | 1 yr | **12%** |
| Anonymous / free / local LLM | — | 0% (free, tulbase only) |

Every new user: **$30 free credit** (90-day expiry), **$10 minimum top-up** (charged $7.50 with a permanent 25% discount on top-ups).

Saving-share is the cut Compresh takes on the **savings** measured against the user's chosen model. The base value comes from the actual model price (when known) or the provider family's cheapest model; anonymous / free-model usage falls back to a flat $0.20 / 1M saved input tokens.

See [compre.sh/pricing](https://compre.sh/pricing) for the canonical pricing page.

## Installation

```bash
pip install compresh-mcp
```

On first run, you'll be prompted for your Compresh API key. If you don't
have an account, your browser opens to [compre.sh/signup](https://compre.sh/signup)
automatically.

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

### Cursor (`~/.cursor/mcp.json`)

Same structure as Claude Code.

### Cowork

Cowork → Settings → Tools → MCP servers → Add:
- Command: `compresh-mcp`
- Environment: `COMPRESH_API_KEY=sk-comp_...`

## Tools exposed

Same four tools as `tulbase-mcp`, with enhanced behavior:

- `compress` — Q-protective compression by default (`protection_mode="balanced"`)
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

`v0.1.0` — first public release, May 2026. Active development. APIs may
change before `v1.0`. Issues and pull requests welcome.

## Links

- [compre.sh](https://compre.sh) — product site
- [Documentation](https://compre.sh/docs) — full reference
- [GitHub](https://github.com/compresh/compresh-mcp) — source
- [Issues](https://github.com/compresh/compresh-mcp/issues) — bug reports
- [tulbase (open core, MIT)](https://github.com/compresh/tulbase) — standalone
