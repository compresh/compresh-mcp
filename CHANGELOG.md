# Changelog

All notable changes to `compresh-mcp` are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versions follow [SemVer](https://semver.org/).

---

## [0.2.5] — 2026-06-09

### Changed

- **Tier check moved before local LexRank (architecture alignment).** The
  open-source tulbase core still runs steps 1–5 (cold storage + anchors) on
  every turn, but the paid path now **defers LexRank summarization**: when
  `/v1/tul1` will run, the summary is produced server-side (LexRank @9), so
  computing it locally too was pure waste. Free / offline usage summarizes
  locally as before (LexRank @6a). On server failure the turns are re-run with
  summarization for the local fallback (idempotent — deterministic entry ids +
  `ON CONFLICT DO NOTHING`, no duplicate rows). Needs `tulbase` with the new
  `Pipeline.run(summarize=...)` flag.
- **Tagless output enforced in the open core.** `tulbase` no longer renders a
  `Q:` distribution line into the model-facing block (a TUL 1.0 / paid remnant;
  the value is in the steered summary, not the tag). Both the local tulbase
  result and the `/v1/tul1` server result are tagless.

## [0.2.4] — 2026-05-21

### Added

- Absolute-size usage reporting (`original_chars` / `tulbase_chars`) in the
  `/v1/usage/report` payload, so the server can compute total savings and the
  free-user portal can display the tulbase saving.

## [0.2.3] — 2026-05-20

### Fixed

- **Net-negative "saving" on short conversations.** When the Compresh
  memory header outweighed the text it elided (typical on conversations
  just past the protection-zone threshold), `tool_compress` reported a
  negative `saving_chars`/`saved_input_tokens`, which dragged down
  dashboard totals (e.g. a session showing −5,016 tokens "saved"). Now
  guarded: if `saving_chars <= 0`, the call returns `applied=False`,
  `reason="net_negative_saving"`, the raw messages untouched, and reports
  `saved=0` telemetry. The calling hook skips context injection in this
  case, so the model just sees the normal conversation.

---

## [0.2.2] — 2026-05-19

### Fixed

- **DuckDB primary-key crash on resumed sessions.** When an MCP host
  resumes the same ``session_id`` after a process restart and re-feeds
  the same conversation history, the resulting compression entries hash
  identically and trigger a ``Constraint Error: Duplicate key …
  violates primary key constraint``. The pipeline now treats this case
  as idempotent: ``compression_log.save()`` uses
  ``INSERT … ON CONFLICT (id) DO NOTHING``. Visible symptom in 0.2.1:
  ``[compresh-mcp] WARNING: pipeline.run failed at turn N: Constraint
  Error: Duplicate key …`` surfaced in the host TUI, and the calling
  hook saw an empty ``compresh_md`` response (silent skip on the
  OpenClaw / Compresh hook).

---

## [0.2.1] — 2026-05-19

### Fixed

- **Log spam in MCP host TUI.** Default log level is now `WARNING` instead
  of `INFO` — pipeline turn-by-turn INFO logs (one line per history turn)
  no longer leak into the host chat surface. Use `--verbose` or
  `COMPRESH_VERBOSE=1` to re-enable INFO output for debugging.

- **DuckDB `.wal` corruption after host restart.** Added `atexit` +
  `SIGTERM` / `SIGINT` / `SIGHUP` handlers that cleanly close every
  per-session DuckDB connection on shutdown. Without this, the host
  restarting our subprocess could leave a partial WAL behind that
  failed to replay on the next compress call.

- **Onboarding URL.** Changed the no-API-key signup link from
  `api.compre.sh/signup?source=compresh-mcp` to
  `compre.sh/signup?source=compresh-mcp` (login + signup moved to the
  apex domain).

### Internal

- `_close_all_sessions()` + `_install_lifecycle_handlers()` in
  `server.py`. Idempotent; safe even if invoked twice on the same
  signal.

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
