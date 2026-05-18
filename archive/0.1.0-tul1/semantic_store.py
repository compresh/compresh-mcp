"""Semantic store — cross-turn dedup for Q3 (fact) sentences.

The TUL 1.0 §4 promise: a fact uttered at T5 and re-uttered (paraphrased
or verbatim) at T20 should not produce two separate entries. Storing
both wastes context tokens; storing one with a hit count delivers the
same information at a fraction of the cost.

Two-tier matching:

  1. **Hash exact match** — normalized content (lowercase, whitespace
     collapsed, punctuation stripped). Fast, deterministic, zero-cost.
     Catches verbatim and trivially-rephrased repeats.

  2. **Semantic embedding cosine** — MiniLM-L6-v2 (sentence-transformers)
     when available, eşik 0.85. Catches paraphrased repeats that the
     hash misses. Optional dependency — if `sentence_transformers` is
     not installed, this tier is skipped and the store falls back to
     hash-only matching.

Storage: DuckDB table `semantic_store`, sibling to `compression_log`.
The two tables share the same `.duckdb` file by default but are
logically independent — semantic_store entries are not compression
artefacts (no cold storage hash) and have their own primary key space.

Per-session by default — a fact uttered by user A in session X is not
deduped against user B's session Y, because conversational context
matters (Tulving's context-trace distinction). Cross-session dedup is
a separate, opt-in mode reserved for future work.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

try:
    import duckdb  # type: ignore
except ImportError:  # pragma: no cover
    duckdb = None  # type: ignore

logger = logging.getLogger(__name__)

# Cosine threshold for semantic match. 0.85 is empirically the sweet
# spot for MiniLM-L6-v2 on factual paraphrases: "Paris is the capital
# of France" ≈ "Fransa'nın başkenti Paris" ≈ "Paris, France's capital".
# Below 0.80 you start matching unrelated facts; above 0.90 you miss
# light paraphrases. Tunable per-deployment.
DEFAULT_COSINE_THRESHOLD = 0.85


# ---------------------------------------------------------------------------
# Normalization for hash-tier
# ---------------------------------------------------------------------------

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace.

    Conservative — does not stem, does not remove stop-words. Two facts
    that share normalized form are very likely the same fact; missing
    paraphrases is left to the semantic tier.
    """
    s = text.strip().lower()
    s = _PUNCT.sub(" ", s)
    s = _WS.sub(" ", s).strip()
    return s


def _normalized_hash(text: str) -> str:
    return hashlib.sha256(_normalize(text).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SemanticEntry:
    """One row in the semantic_store table."""

    id: str
    session_id: str
    content: str
    normalized_hash: str
    first_seen_turn: int
    hit_count: int = 1
    embedding: Optional[list[float]] = None
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MatchResult:
    """What SemanticStore.find_match returns."""

    matched: bool
    entry: Optional[SemanticEntry] = None
    similarity: float = 0.0     # 1.0 for exact, [0, 1) for semantic
    match_tier: str = "none"    # "hash" | "semantic" | "none"


# ---------------------------------------------------------------------------
# Embedding helper (lazy load)
# ---------------------------------------------------------------------------


_EMBED_MODEL = None
_EMBED_AVAILABLE: Optional[bool] = None


def _get_embedder():
    """Return a sentence-transformer model or None if unavailable."""
    global _EMBED_MODEL, _EMBED_AVAILABLE
    if _EMBED_AVAILABLE is False:
        return None
    if _EMBED_MODEL is not None:
        return _EMBED_MODEL
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
    except ImportError:
        logger.info(
            "sentence-transformers not installed — semantic tier disabled, "
            "falling back to hash-only dedup"
        )
        _EMBED_AVAILABLE = False
        return None
    try:
        _EMBED_MODEL = SentenceTransformer("all-MiniLM-L6-v2")
        _EMBED_AVAILABLE = True
        return _EMBED_MODEL
    except Exception as e:  # pragma: no cover — download / load failures
        logger.warning("failed to load MiniLM (%s) — semantic tier disabled", e)
        _EMBED_AVAILABLE = False
        return None


def _embed(text: str) -> Optional[list[float]]:
    model = _get_embedder()
    if model is None:
        return None
    try:
        vec = model.encode([text], normalize_embeddings=True)[0]
        return [float(x) for x in vec]
    except Exception as e:  # pragma: no cover
        logger.warning("embed failed: %s", e)
        return None


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Dot product of two L2-normalized vectors = cosine similarity."""
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))


# ---------------------------------------------------------------------------
# Schema + CRUD
# ---------------------------------------------------------------------------


class SemanticStore:
    """DuckDB-backed cross-turn semantic dedup store.

    Share the proxy's DuckDB connection or open a new file by path.
    """

    _SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS semantic_store (
      id               TEXT PRIMARY KEY,
      session_id       TEXT NOT NULL,
      content          TEXT NOT NULL,
      normalized_hash  TEXT NOT NULL,
      first_seen_turn  INTEGER NOT NULL,
      hit_count        INTEGER NOT NULL DEFAULT 1,
      embedding        TEXT,           -- JSON array; nullable when MiniLM missing
      created_at       TIMESTAMP NOT NULL,
      metadata         JSON
    );
    """

    _INDEX_SQL = (
        "CREATE INDEX IF NOT EXISTS idx_semantic_session_hash "
        "ON semantic_store(session_id, normalized_hash);",
        "CREATE INDEX IF NOT EXISTS idx_semantic_session "
        "ON semantic_store(session_id);",
    )

    def __init__(
        self,
        conn_or_path: "duckdb.DuckDBPyConnection | str",
        *,
        cosine_threshold: float = DEFAULT_COSINE_THRESHOLD,
    ):
        if duckdb is None:
            raise RuntimeError(
                "duckdb is not installed. Install it via `pip install duckdb`."
            )
        if not 0.0 <= cosine_threshold <= 1.0:
            raise ValueError("cosine_threshold must be in [0, 1]")
        if isinstance(conn_or_path, str):
            self._conn = duckdb.connect(conn_or_path)
            self._owns_conn = True
        else:
            self._conn = conn_or_path
            self._owns_conn = False
        self.cosine_threshold = cosine_threshold

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------
    def ensure_schema(self) -> None:
        self._conn.execute(self._SCHEMA_SQL)
        for idx in self._INDEX_SQL:
            self._conn.execute(idx)
        logger.info("semantic_store schema ensured")

    # ------------------------------------------------------------------
    # Find / save
    # ------------------------------------------------------------------
    def find_match(
        self, content: str, *, session_id: str,
    ) -> MatchResult:
        """Two-tier match: hash exact, then optional semantic cosine.

        Returns MatchResult(matched=False) when nothing matches.
        """
        if not (content or "").strip():
            return MatchResult(matched=False)

        # --- Tier 1: hash ---
        norm_hash = _normalized_hash(content)
        row = self._conn.execute(
            """
            SELECT id, session_id, content, normalized_hash,
                   first_seen_turn, hit_count, embedding, created_at, metadata
            FROM semantic_store
            WHERE session_id = ? AND normalized_hash = ?
            LIMIT 1;
            """,
            [session_id, norm_hash],
        ).fetchone()
        if row is not None:
            entry = self._row_to_entry(row)
            return MatchResult(
                matched=True, entry=entry, similarity=1.0, match_tier="hash"
            )

        # --- Tier 2: semantic embedding ---
        embedder = _get_embedder()
        if embedder is None:
            return MatchResult(matched=False)
        query_vec = _embed(content)
        if query_vec is None:
            return MatchResult(matched=False)

        # Scan candidate rows (per-session). For early TUL 1.0 a linear
        # scan is acceptable; the store size is bounded by Q3 sentences
        # per session, typically O(100) — well within a millisecond
        # range. A vector-index DuckDB extension can be added later.
        rows = self._conn.execute(
            """
            SELECT id, session_id, content, normalized_hash,
                   first_seen_turn, hit_count, embedding, created_at, metadata
            FROM semantic_store
            WHERE session_id = ? AND embedding IS NOT NULL;
            """,
            [session_id],
        ).fetchall()
        best_score = 0.0
        best_entry: Optional[SemanticEntry] = None
        for row in rows:
            entry = self._row_to_entry(row)
            if not entry.embedding:
                continue
            score = _cosine(query_vec, entry.embedding)
            if score > best_score:
                best_score = score
                best_entry = entry
        if best_entry is not None and best_score >= self.cosine_threshold:
            return MatchResult(
                matched=True,
                entry=best_entry,
                similarity=best_score,
                match_tier="semantic",
            )
        return MatchResult(matched=False, similarity=best_score)

    def save(
        self,
        content: str,
        *,
        session_id: str,
        turn_idx: int,
        metadata: Optional[dict[str, Any]] = None,
    ) -> SemanticEntry:
        """Insert a new semantic_store entry (no dedup check).

        Callers normally do `find_match` first; if no match, then `save`.
        """
        norm_hash = _normalized_hash(content)
        entry_id = f"sem-{session_id}-T{turn_idx}-{norm_hash[:8]}"
        embedding = _embed(content)
        entry = SemanticEntry(
            id=entry_id,
            session_id=session_id,
            content=content,
            normalized_hash=norm_hash,
            first_seen_turn=turn_idx,
            hit_count=1,
            embedding=embedding,
            metadata=dict(metadata or {}),
        )
        self._conn.execute(
            """
            INSERT INTO semantic_store
              (id, session_id, content, normalized_hash, first_seen_turn,
               hit_count, embedding, created_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            [
                entry.id, entry.session_id, entry.content,
                entry.normalized_hash, entry.first_seen_turn,
                entry.hit_count,
                json.dumps(embedding) if embedding is not None else None,
                entry.created_at,
                json.dumps(entry.metadata) if entry.metadata else None,
            ],
        )
        return entry

    def bump_hit(self, entry_id: str) -> int:
        """Increment hit_count for an existing entry. Returns new count."""
        self._conn.execute(
            "UPDATE semantic_store SET hit_count = hit_count + 1 "
            "WHERE id = ?;",
            [entry_id],
        )
        row = self._conn.execute(
            "SELECT hit_count FROM semantic_store WHERE id = ?;",
            [entry_id],
        ).fetchone()
        return int(row[0]) if row else 0

    def find_or_save(
        self,
        content: str,
        *,
        session_id: str,
        turn_idx: int,
        metadata: Optional[dict[str, Any]] = None,
    ) -> tuple[SemanticEntry, MatchResult]:
        """Convenience: find_match, then save if no match.

        Returns ``(entry, match_result)`` — when match_result.matched is
        True, ``entry`` is the *existing* entry (hit count bumped); when
        False, ``entry`` is the newly-saved one.
        """
        match = self.find_match(content, session_id=session_id)
        if match.matched and match.entry is not None:
            self.bump_hit(match.entry.id)
            # Refresh hit count for caller.
            match.entry.hit_count += 1
            return match.entry, match
        new_entry = self.save(
            content,
            session_id=session_id,
            turn_idx=turn_idx,
            metadata=metadata,
        )
        return new_entry, match

    # ------------------------------------------------------------------
    # Stats / debug
    # ------------------------------------------------------------------
    def stats(self, session_id: str) -> dict[str, Any]:
        row = self._conn.execute(
            """
            SELECT COUNT(*), SUM(hit_count),
                   SUM(CASE WHEN hit_count > 1 THEN hit_count - 1 ELSE 0 END)
            FROM semantic_store
            WHERE session_id = ?;
            """,
            [session_id],
        ).fetchone()
        n, total_hits, dedup_saves = row if row else (0, 0, 0)
        return {
            "n_entries": int(n or 0),
            "total_hits": int(total_hits or 0),
            "dedup_saves": int(dedup_saves or 0),
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _row_to_entry(self, row: Sequence[Any]) -> SemanticEntry:
        (
            id_, sid, content, norm_hash, first_seen, hit, emb,
            created, meta,
        ) = row
        embedding: Optional[list[float]] = None
        if emb is not None:
            try:
                embedding = json.loads(emb) if isinstance(emb, str) else list(emb)
            except (json.JSONDecodeError, TypeError):
                embedding = None
        metadata: dict[str, Any] = (
            json.loads(meta) if isinstance(meta, str) and meta
            else (meta or {})
        )
        return SemanticEntry(
            id=id_, session_id=sid, content=content,
            normalized_hash=norm_hash,
            first_seen_turn=int(first_seen),
            hit_count=int(hit),
            embedding=embedding,
            created_at=(
                created if isinstance(created, datetime)
                else datetime.fromisoformat(str(created))
            ),
            metadata=metadata,
        )

    def close(self) -> None:
        if self._owns_conn:
            self._conn.close()
