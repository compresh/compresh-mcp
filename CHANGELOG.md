# Changelog

All notable changes to `compresh-mcp` are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versions follow [SemVer](https://semver.org/).

---

## [0.2.0] — 2026-05-18

### Changed (breaking)

- **TUL 1.0 layers moved server-side.** Q-protective sentence ranking,
  epistemic markers, and the semantic store are no longer shipped in
  the local Python package. They run on the Compresh server via the
  new `/v1/tul1` endpoint, gated by a valid Compresh API key + tier.

- Local pipeline now uses the base tulbase `Tier1Summarizer()` (LexRank
  + heuristic carry-out / opens) — no Q matrix, no epistemic classifier.

- Removed `compresh_mcp.tul1` namespace. Code preserved at
  `archive/0.1.0-tul1/` for reference, not shipped in the wheel.

### Added

- `compresh_mcp.tul1_client.call_v1_tul1()` — async HTTP client for the
  paid `/v1/tul1` endpoint with rich error types (`Tul1PaymentRequired`,
  `Tul1NetworkError`, `Tul1ServerError`).

- `tool_compress` now overlays server-side TUL 1.0 enhancement on top
  of the local tulbase result. On server failure, falls back to local
  silently — local compression always works.

- Response payload includes `tul1_server_used: bool`,
  `tul1_payment_required: bool`, `tul1_error: str | null` so callers
  can surface why the server overlay was (or wasn't) applied.

### Migration

`compresh-mcp@0.1.0` shipped TUL 1.0 classifiers inside the local
Python package. That violated our pricing matrix promise that "free →
tulbase only, Pro → TUL 1.0" and is the reason `0.1.0` was yanked from
PyPI. Existing installs continue to work but should upgrade:

```bash
pip install --upgrade compresh-mcp
```

If you depended on `from compresh_mcp.tul1 import QMatrixClassifier`
in your own code, the same classifier is now reachable only through
the server. Either:

1. Call `/v1/tul1` directly (see `tul1_client.call_v1_tul1`), or
2. Vendor the archived code from
   `https://github.com/compresh/compresh-mcp/tree/main/archive/0.1.0-tul1`
   (BUSL-1.1 — see the LICENSE for terms; commercial use requires a
   Compresh subscription).

---

## [0.1.0] — 2026-05-18 (YANKED)

Initial public release. Yanked on 2026-05-18 due to TUL 1.0 layers
being shipped client-side by mistake. See `0.2.0` migration note.
