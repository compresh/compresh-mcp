"""Tier-1 deterministic summarizer.

Produces a turn-box summary plus structured fields (`opens`, `resolves`,
`carry_in`, `carry_out`) without calling an LLM. Falls back gracefully
when optional dependencies (`sumy`) are unavailable.

Design (TUL 1.0 §5):

  - Sentence-level LexRank (sumy) selects the top-N sentences.
  - Summary HARD CAP at ``_SUMMARY_MAX_CHARS`` to keep TurnBox compact;
    avoids the pathological case where one very long sentence becomes
    the entire summary verbatim.
  - `opens`: heuristic — sentences that contain a question mark or one
    of the question stems (TR + EN). Capped at ``_OPENS_MAX``.
  - `resolves`: previous-turn `opens` that share named-entity tokens
    with the current summary (intersection cardinality ≥ 1).
  - `carry_out`: named entities (capitalized tokens, identifiers) that
    appeared in the summary and are likely needed downstream. Capped at
    ``_CARRY_MAX`` to prevent long lists in entity-heavy domains
    (e.g. clinical Q&A with many diagnosis names).

This is deliberately humble. Tier-2 will swap in a tiny LLM when Tier-1
confidence drops below 0.5.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

logger = logging.getLogger(__name__)


# Q-protective compression modes — patent claim ek başvuru adayı.
# Sıkıştırma sırasında hangi Q sınıflarının ranking'inde priority alacağı.
PROTECT_MODES = {
    "off": {"quadrants": set(), "include_non_vr": False},
    "conservative": {"quadrants": {"Q3"}, "include_non_vr": False},
    "balanced": {"quadrants": {"Q3"}, "include_non_vr": True},
    "aggressive": {"quadrants": {"Q3", "Q4"}, "include_non_vr": True},
}

# Question detection. Stricter than substring match to avoid false
# positives like "düşüktü, where %0.9 idi" (where mid-sentence in TR prose).
# A sentence is treated as a question only if:
#   1) it ends with "?" (universal, strongest signal), OR
#   2) it STARTS with a question stem (TR/EN), OR
#   3) Turkish "mi/mı/mu/mü" question particle as a free-standing word
#      somewhere in the sentence (e.g. "Geliyor musun?", "Bitti mi?").
_QUESTION_START_RE = re.compile(
    r"^\s*(?:nasıl|niye|neden|nerede|kim|kaç|hangi|"
    r"how|why|when|where|who|which|what|ne\s+zaman)\b",
    re.IGNORECASE,
)
_TR_PARTICLE_RE = re.compile(r"\b(?:mi|mı|mu|mü|musun|misin|mıydı|mıymış)\b", re.IGNORECASE)


def _is_question(sentence: str) -> bool:
    s = sentence.strip()
    if not s:
        return False
    if s.rstrip().endswith("?"):
        return True
    if _QUESTION_START_RE.match(s):
        return True
    if _TR_PARTICLE_RE.search(s):
        return True
    return False

# Sentence boundary regex. Doesn't try to be perfect — handles `.`, `?`, `!`,
# Turkish ellipsis, and newlines.
_SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+|\n{2,}")

# Cap-token = entity heuristic. Picks `Phase`, `Compresh`, `gpt-4o-mini`, etc.
_ENTITY_RE = re.compile(r"\b(?:[A-ZŞÖÇĞÜİ][\w\-]+|[a-z][a-z0-9]*-[a-z0-9\-]+)\b")

# TUL 1.0 format-tightening caps. The TurnBox markdown must stay
# compact — otherwise on pure-dialog conversations (no modality content
# to compress) the box bloats raw history. Pilot bench Conv A (psychology
# Q&A, 100 turns) showed -50% saving without these caps because:
#   - one long user message → one-sentence summary → 582-char "Summary:" line
#   - 13 diagnosis names → 13-token carry_out list
# Caps keep TurnBox bounded regardless of input shape.
_SUMMARY_MAX_CHARS = 200      # hard cut for Summary: line
_CARRY_MAX = 5                # max entities in carry_out
_OPENS_MAX = 3                # max questions in opens
_RESOLVES_MAX = 3             # max links in resolves


def _truncate_at_word(text: str, max_chars: int) -> str:
    """Cut at a word boundary near max_chars, append ellipsis if cut."""
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars].rsplit(" ", 1)[0]
    cut = cut.rstrip(",;:-")
    return cut + "…"


@dataclass(slots=True)
class SummarizerResult:
    """Everything the pipeline needs from Tier-1 summarization."""

    summary: str
    opens: list[str] = field(default_factory=list)
    resolves: list[str] = field(default_factory=list)
    carry_out: list[str] = field(default_factory=list)
    confidence: float = 0.95


class Tier1Summarizer:
    """Deterministic summarizer.

    Parameters
    ----------
    sentences_count:
        Target number of sentences in the summary. Default 2 — short enough
        to keep summaries within the 30-50 token budget from the spec.
    summary_max_chars:
        Hard cap on the summary length (default 200). Used for "calibrated"
        compression modes where TUL 1.0 tag overhead needs to be offset.
        E.g. setting to 120 yields ~%40 smaller TurnBox at the cost of
        coarser summary granularity.
    carry_max:
        Hard cap on carry_out entity list (default 5).
    protect_mode:
        Q-protective compression knob (default "off" — backward compatible).
        ``"conservative"``: Q3 (fact, verified) cümleler ranking'inde
        öncelik kazanır.
        ``"balanced"``: Q3 + non-VR (HR/CR/UC) sapma cümleler öncelikli.
        ``"aggressive"``: Q3 + Q4 + non-VR cümleler hepsi öncelikli.
        Protected cümleler `sentences_count` cap'i içinde önce seçilir,
        kalan slot LexRank score'uyla doldurulur. Cap delmez.
    q_classifier / epi_classifier:
        Opsiyonel. ``protect_mode != "off"`` ise zorunlu. Dış'ardan inject
        edilir ki test/bench reproducibility için tek instance paylaşılsın.
    """

    def __init__(
        self,
        *,
        sentences_count: int = 2,
        summary_max_chars: int = _SUMMARY_MAX_CHARS,
        carry_max: int = _CARRY_MAX,
        protect_mode: str = "off",
        q_classifier=None,
        epi_classifier=None,
    ):
        if sentences_count < 1:
            raise ValueError("sentences_count must be >= 1")
        if summary_max_chars < 20:
            raise ValueError("summary_max_chars must be >= 20")
        if carry_max < 0:
            raise ValueError("carry_max must be >= 0")
        if protect_mode not in PROTECT_MODES:
            raise ValueError(
                f"protect_mode must be one of {list(PROTECT_MODES)}; "
                f"got {protect_mode!r}"
            )
        if protect_mode != "off" and q_classifier is None:
            # Auto-init defaults — caller can still override.
            from .q_matrix import QMatrixClassifier
            q_classifier = QMatrixClassifier()
        if protect_mode != "off" and (
            PROTECT_MODES[protect_mode]["include_non_vr"]
            and epi_classifier is None
        ):
            from .epistemic import EpistemicClassifier
            epi_classifier = EpistemicClassifier()

        self.sentences_count = sentences_count
        self.summary_max_chars = summary_max_chars
        self.carry_max = carry_max
        self.protect_mode = protect_mode
        self.protect_config = PROTECT_MODES[protect_mode]
        self.q_classifier = q_classifier
        self.epi_classifier = epi_classifier
        self._lexrank = self._try_load_lexrank()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def run(
        self,
        dialog_text: str,
        *,
        prev_opens: Optional[Sequence[str]] = None,
        prev_carry_out: Optional[Sequence[str]] = None,
    ) -> SummarizerResult:
        """Summarize `dialog_text`. Empty / whitespace-only → empty result."""
        text = (dialog_text or "").strip()
        if not text:
            return SummarizerResult(summary="", confidence=0.0)

        sentences = self._split_sentences(text)
        if not sentences:
            return SummarizerResult(summary="", confidence=0.0)

        # 1) Pick top-N sentences (LexRank if available, else first-N).
        chosen = self._rank_sentences(sentences)
        summary = " ".join(chosen).strip()
        # Hard cap — keep TurnBox compact even when input is a single
        # very long sentence (Tier-1 has no way to split inside a
        # sentence; a Tier-2 LLM-light pass would).
        summary = _truncate_at_word(summary, self.summary_max_chars)

        # 2) opens — questions in the *full* dialog (not just chosen).
        opens = self._extract_opens(sentences)[:_OPENS_MAX]

        # 3) resolves — intersection of carry_out tokens with prev_opens.
        resolves = self._match_resolves(summary, prev_opens or [])[:_RESOLVES_MAX]

        # 4) carry_out — named entities that appeared in summary, plus any
        # carry-forward from prev (still relevant if mentioned again).
        carry_out = self._extract_entities(summary)
        if prev_carry_out:
            for tok in prev_carry_out:
                if tok in summary and tok not in carry_out:
                    carry_out.append(tok)
        carry_out = carry_out[:self.carry_max]

        # Confidence: heuristic — drop when summary is suspiciously short.
        confidence = 0.95 if len(summary) >= 20 else 0.5

        return SummarizerResult(
            summary=summary,
            opens=opens,
            resolves=resolves,
            carry_out=carry_out,
            confidence=confidence,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _try_load_lexrank(self):
        """Try to import sumy LexRank. Returns a callable or None."""
        try:
            from sumy.parsers.plaintext import PlaintextParser  # type: ignore
            from sumy.nlp.tokenizers import Tokenizer  # type: ignore
            from sumy.summarizers.lex_rank import LexRankSummarizer  # type: ignore

            def _run(text: str, n: int) -> list[str]:
                parser = PlaintextParser.from_string(text, Tokenizer("english"))
                summ = LexRankSummarizer()
                return [str(s) for s in summ(parser.document, n)]

            return _run
        except Exception as e:  # pragma: no cover — optional dep
            logger.info("sumy not available, falling back to first-N (%s)", e)
            return None

    def _split_sentences(self, text: str) -> list[str]:
        parts = [p.strip() for p in _SENT_SPLIT.split(text) if p.strip()]
        return parts

    def _rank_sentences(self, sentences: list[str]) -> list[str]:
        n = min(self.sentences_count, len(sentences))
        if n <= 0:
            return []

        # Q-protective path — if a protect_mode is active, bias the
        # ranking toward sentences whose Q class (and optionally
        # epistemic marker) matches the protection set.
        if self.protect_mode != "off" and self.q_classifier is not None:
            return self._rank_sentences_q_aware(sentences, n)

        # Default LexRank path (backward compatible).
        if self._lexrank is not None and len(sentences) > n:
            try:
                joined = " ".join(sentences)
                ranked = self._lexrank(joined, n)
                if ranked:
                    return ranked
            except Exception as e:  # pragma: no cover — sumy edge cases
                logger.warning("LexRank failed, using first-N (%s)", e)
        return sentences[:n]

    # ------------------------------------------------------------------
    # Q-protective ranking
    # ------------------------------------------------------------------
    def _is_sentence_protected(self, sentence: str) -> bool:
        """Mode'a göre cümle koruma listesinde mi?

        Conservative: Q3 + VR (default — yani Q3 fact, hedge YOK)
        Balanced:     Q3 (her epi) + Q3-non-VR ya da Q4-non-VR
                      → "epistemic-rich" cümleler (HR/CR/UC) öncelikli
        Aggressive:   Q3 + Q4 + non-VR sapma cümleler
        """
        cfg = self.protect_config
        try:
            q_pairs = self.q_classifier.classify_text_pairs(sentence)
            if not q_pairs:
                return False
            q = q_pairs[0][1].quadrant  # tek cümle → tek verdict
        except Exception:
            return False

        # Q quadrant matches the protected set?
        if q in cfg["quadrants"]:
            return True

        # Non-VR epistemic boost — sadece include_non_vr modlarda
        if cfg["include_non_vr"] and self.epi_classifier is not None:
            try:
                e_pairs = self.epi_classifier.classify_text_pairs(sentence)
                if e_pairs and not e_pairs[0][1].marker.is_default:
                    return True
            except Exception:
                pass

        return False

    def _rank_sentences_q_aware(
        self, sentences: list[str], n: int,
    ) -> list[str]:
        """Q-aware ranking: protected cümleler önce seçilir, kalan slot
        LexRank order'ıyla doldurulur. Sentences_count cap'i korunur."""
        # 1) Tüm cümleleri sınıflandır (protected mı?)
        protected_mask = [self._is_sentence_protected(s) for s in sentences]
        protected_idxs = [i for i, p in enumerate(protected_mask) if p]
        unprotected_idxs = [i for i, p in enumerate(protected_mask) if not p]

        # 2) LexRank top-K çek (yeterince geniş bir set), order
        # information için. Tüm cümleler ranking sırasında kullanılır.
        lex_order: list[int] = []
        if self._lexrank is not None and len(sentences) > 1:
            try:
                joined = " ".join(sentences)
                ranked_texts = self._lexrank(joined, len(sentences))
                # Map back to indices (first match per text)
                used: set[int] = set()
                for t in ranked_texts:
                    for i, s in enumerate(sentences):
                        if i in used:
                            continue
                        if s.strip() == str(t).strip():
                            lex_order.append(i)
                            used.add(i)
                            break
                # Add any sentence LexRank didn't return at end
                for i in range(len(sentences)):
                    if i not in used:
                        lex_order.append(i)
            except Exception as e:  # pragma: no cover
                logger.warning("LexRank Q-aware failed, fallback (%s)", e)
                lex_order = list(range(len(sentences)))
        else:
            lex_order = list(range(len(sentences)))

        # 3) Selection: önce protected (LexRank ranking sırasında), sonra
        # kalan slot için unprotected (yine LexRank sırasında).
        selected_idxs: list[int] = []
        # Protected cümleleri LexRank order'da topla
        for i in lex_order:
            if i in protected_idxs and len(selected_idxs) < n:
                selected_idxs.append(i)
        # Slot kaldıysa unprotected'lardan ekle
        for i in lex_order:
            if len(selected_idxs) >= n:
                break
            if i not in selected_idxs:
                selected_idxs.append(i)

        # 4) Original cümle sırasına göre döndür (TurnBox akıcılığı için)
        selected_idxs.sort()
        return [sentences[i] for i in selected_idxs]

    def _extract_opens(self, sentences: Iterable[str]) -> list[str]:
        opens: list[str] = []
        for s in sentences:
            if _is_question(s):
                opens.append(s.strip())
        # Dedup while preserving order.
        seen: set[str] = set()
        return [o for o in opens if not (o in seen or seen.add(o))]

    def _extract_entities(self, text: str) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for m in _ENTITY_RE.finditer(text):
            tok = m.group(0)
            if tok.lower() in {"the", "and", "but"}:
                continue
            if tok in seen:
                continue
            seen.add(tok)
            out.append(tok)
        return out

    def _match_resolves(
        self,
        summary: str,
        prev_opens: Sequence[str],
    ) -> list[str]:
        if not prev_opens:
            return []
        summary_ents = set(self._extract_entities(summary))
        if not summary_ents:
            return []
        out: list[str] = []
        for q in prev_opens:
            q_ents = set(self._extract_entities(q))
            if q_ents & summary_ents:
                out.append(q)
        return out
