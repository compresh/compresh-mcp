# compresh-mcp

**MCP server for Compresh** — production-grade context compression for LLM agent conversations.

> Compresh adds Q-protective ranking, epistemic marker classification, and
> depth-aware adaptation on top of the open-source [`tulbase`](https://github.com/compresh/tulbase)
> compression core. This is the **paid tier** distribution.

This package bundles `tulbase` (MIT) as `compresh_mcp.tulbase`. You can also
install it standalone via `pip install tulbase` if you only need the free
open-core layer. See [NOTICES.md](./NOTICES.md) for vendoring details.

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

See [https://compre.sh/pricing](https://compre.sh/pricing). Three tiers:

- **Tier-A** (integrated MCP/OAuth metadata, e.g. Cowork, Claude Code, Cursor):
  saving-share **%25** on actual model cost
- **Tier-B** (family-level provider declaration):
  saving-share **%25** on the family's cheapest model price
- **Tier-C** (anonymous / local LLM / free models):
  flat **$0.20 per 1M saved input tokens**

Every new user: **$30 free credit** (90-day expiry), **$10 minimum budget**
(charged $7.5 after standard %25 discount).

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
