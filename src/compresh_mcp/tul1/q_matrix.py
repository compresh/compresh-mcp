"""Q matrix classifier — episodic × affective sentence classification.

Implements TUL 1.0 §3 (Tulving-grounded categorization). Each input
sentence is mapped to one of four quadrants:

```
                    AFFECTIVE
                  düşük          yüksek
              ┌─────────────┬─────────────┐
EPISODIC      │  Q1: Olay   │ Q2: Anı     │
  yüksek      │   (event)   │  (memory)   │
              ├─────────────┼─────────────┤
EPISODIC      │  Q3: Fact   │ Q4: Yargı   │
  düşük       │             │  (opinion)  │
              └─────────────┴─────────────┘
```

The classification uses *two* parallel deterministic signals (no LLM
call by default):

  1. **Episodic axis** — first-person markers, past tense, spatio-
     temporal anchors, memory verbs, sensory verbs. High score → the
     sentence describes a personal experience.

  2. **Affective axis** — polarity lexicon (positive / negative words),
     subjectivity markers ("I think", "in my opinion", "feel"), emotion
     words, intensifiers. High score → the sentence carries feeling.

Both signals are normalized to [0, 1]. Default threshold = 0.30 on each
axis; below = "low", at-or-above = "high".

Why a custom rule-based pass instead of TextBlob / VADER alone?

  - Tulbase is deterministic and offline. TextBlob / VADER add ~30 MB
    of NLTK corpora and don't speak Turkish.
  - The signals we need (first-person + tense + spatial anchors) are
    not what mainstream sentiment toolkits expose.
  - A Tier-2 LLM fallback (Phase 2.3) can be added later when
    confidence is low.

The lexicons are bilingual (EN + TR). For other languages the
classifier currently degrades to Q3 (low score on both axes) — safe
default since Q3 entries get the most aggressive dedup.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Sequence

# ---------------------------------------------------------------------------
# Lexicons
# ---------------------------------------------------------------------------
#
# Kept short, opinionated, and high-precision. Misses are fine
# (defaults to Q3 — semantic). False positives are not (Q3 entries get
# dedup'd, which could lose information if the entry is actually
# episodic).

# --- Episodic axis ----------------------------------------------------------

# First-person markers (EN + TR pronouns; TR is morphology-heavy so we
# also catch the verb suffixes via regex below).
_FIRST_PERSON = {
    # English
    "i", "i'm", "i've", "i'd", "i'll", "me", "my", "mine", "myself",
    "we", "we're", "we've", "us", "our", "ours", "ourselves",
    # Turkish
    "ben", "bana", "beni", "benim", "bende",
    "biz", "bize", "bizi", "bizim", "bizde",
}

# Turkish past-tense / memory-personal suffixes — coarse pattern, used as
# additional first-person signal when the pronoun is implicit.
_TR_PERSONAL_SUFFIX = re.compile(
    r"\w+("
    r"d[ıiuü]m|d[ıiuü]k|t[ıiuü]m|t[ıiuü]k|"     # past-tense first-person sg/pl
    r"miş[ıiuü]m|muş[ıiuü]m|"                    # narrative past
    r"yorum|yoruz"                                # progressive first-person
    r")\b",
    re.IGNORECASE,
)

# Turkish possessive suffixes — "eşim", "evim", "annem", "babamız".
# Distinct from personal-verb suffixes; signals personal-domain content
# even when no verb morphology is present ("Eşim hastaneye gitti").
# Root ≥ 2 chars to catch short nouns like "eş+im", "ev+im".
_TR_POSSESSIVE_SUFFIX = re.compile(
    r"\b\w{2,}("
    r"[ıiuü]m|[ıiuü]n|[ıiuü]m[ıiuü]z|[ıiuü]n[ıiuü]z"   # 1st/2nd person sg/pl
    r")\b",
    re.IGNORECASE,
)

# Memory verbs — strong episodic + affective signal.
# Excludes "think/thinking/thought" — those are mental verbs but more
# often signal opinion ("I think we should…") than memory recall.
# Memory verbs are also bonus-counted on the affective axis because
# remembering / forgetting / longing all carry emotional weight (see
# Tulving's autonoetic awareness).
_MEMORY_VERBS = {
    # English
    "remember", "recall", "forget", "forgot", "remembered", "recalled",
    "reminisce", "reminisced", "reminded", "recollect", "recollected",
    "miss", "missed", "missing",
    # Turkish
    "hatırlıyorum", "hatırlıyoruz", "hatırlamıyorum", "hatırlarım",
    "unutamıyorum", "unutmuşum", "unutmadım", "unutamadım",
    "anımsıyorum", "anımsamıyorum", "özlüyorum", "özledim",
}

# Mental / opinion verbs — NOT memory. Signal subjectivity (affective)
# but not episodic recall.
_OPINION_VERBS = {
    "think", "thinking", "thought", "believe", "believed", "believes",
    "suppose", "supposed", "guess", "guessed",
    "düşünüyorum", "düşünmüyorum", "sanıyorum", "tahminimce",
    "kanaatimce",
}

# Sensory verbs — first-person experience.
_SENSORY_VERBS = {
    "saw", "heard", "felt", "smelled", "tasted", "watched", "noticed",
    "gördüm", "duydum", "kokladım", "tattım", "izledim", "fark ettim",
    "hissettim",
}

# English past-tense verb markers (coarse — caught by suffix patterns).
_EN_PAST_VERB_SUFFIX = re.compile(r"\b\w+(ed)\b", re.IGNORECASE)
# Specific past-tense English forms.
_EN_PAST_FORMS = {
    "was", "were", "had", "did", "went", "came", "gave", "took",
    "made", "said", "told", "knew", "thought", "saw", "got",
}

# Temporal anchors — "yesterday", "last week", "in 2019", etc.
_TEMPORAL_ANCHOR_RE = re.compile(
    r"\b("
    r"yesterday|today|tonight|tomorrow|last\s+(?:night|week|month|year)|"
    r"\d{4}'?(?:de|da|te|ta)?|"           # year mentions, optional TR locative
    r"dün|bugün|yarın|"
    r"geçen\s+(?:gece|hafta|ay|yıl|sene)|"
    r"on\s+\w+day|"                        # "on Monday"
    r"saat\s+\d|"                          # "saat 9"
    r"\d{1,2}:\d{2}"                       # times
    r")\b",
    re.IGNORECASE,
)

# Spatial anchors — "in Paris", "at the office", etc.
_SPATIAL_ANCHOR_RE = re.compile(
    r"\b("
    r"in\s+(?:the\s+)?[A-Z][\wÇĞIİÖŞÜ]+|"   # "in Paris", "in the office"
    r"at\s+(?:the\s+)?[A-Z][\wÇĞIİÖŞÜ]+|"
    r"[A-ZÇĞIİÖŞÜ][\wÇĞIİÖŞÜ]+'?(?:de|da|te|ta)\b"  # "Paris'te", "ofiste"
    r")\b",
)

# --- Affective axis --------------------------------------------------------

# High-arousal positive words.
_POS_WORDS = {
    "love", "loved", "loves", "amazing", "wonderful", "fantastic",
    "great", "good", "happy", "joy", "joyful", "excited", "exciting",
    "beautiful", "delight", "delightful", "fond", "cherish",
    # Turkish
    "harika", "muhteşem", "güzel", "muhteşemdi", "harikaydı",
    "sevdim", "sevdiğim", "mutlu", "mutluyum", "keyifli", "neşeli",
    "umut", "umutlu",
}

# High-arousal negative words.
_NEG_WORDS = {
    "hate", "hated", "hates", "terrible", "awful", "sad", "sadly",
    "angry", "anger", "afraid", "fear", "fearful", "scared", "horrible",
    "regret", "regretted", "miss", "missed", "lonely", "alone",
    # Turkish
    "korkunç", "kötü", "berbat", "üzgün", "üzgünüm", "kızgın", "kızgınım",
    "korktum", "korkuyorum", "nefret", "yalnız", "yalnızım",
    "pişman", "özledim", "özlüyorum",
}

# Subjectivity markers — explicit "I think / believe / feel" surface.
_SUBJECTIVITY_PHRASES = {
    "i think", "i believe", "i feel", "i suppose", "i guess",
    "in my opinion", "i'd say", "i would say",
    "bence", "bana göre", "sanırım", "düşünüyorum",
    "kanaatimce", "hissimce", "duygusal olarak",
}

# Emotion vocabulary (Plutchik-ish, light).
_EMOTION_WORDS = {
    "joy", "sadness", "anger", "fear", "surprise", "disgust",
    "trust", "anticipation", "nostalgia", "longing", "shame",
    "guilt", "pride", "envy",
    "sevinç", "üzüntü", "öfke", "korku", "şaşkınlık", "iğrenme",
    "güven", "umut", "nostalji", "özlem", "utanç", "suçluluk",
    "gurur", "kıskançlık",
}

# Intensifiers / exclamations — boost the affective score.
_INTENSIFIERS = {
    "really", "very", "so", "extremely", "absolutely", "totally",
    "incredibly", "deeply", "truly",
    "çok", "gerçekten", "son derece", "fazlasıyla", "tamamen",
    "inanılmaz",
}

# Universal value statements — Q4-like content. Covers modals
# (should/must/ought), obligations (need to / have to), and absolutes
# (always/never). These don't carry sentiment polarity by themselves
# but signal that the sentence is taking a normative stance.
_VALUE_STATEMENT_RE = re.compile(
    r"\b("
    r"should|ought\s+to|must|always|never|"
    r"need\s+to|needs\s+to|have\s+to|has\s+to|had\s+to|gotta|"
    r"meli|malı|gerek|asla|hep|hiçbir\s+zaman|"
    r"zorunda|şart"
    r")\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

# Word boundary tokenizer that preserves apostrophes inside English
# contractions ("i'm") and turkish suffixed proper nouns ("paris'te").
_WORD_RE = re.compile(r"[A-Za-zÇĞIİÖŞÜçğıiöşü'\-]+", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    return [m.group(0).lower() for m in _WORD_RE.finditer(text)]


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


Quadrant = Literal["Q1", "Q2", "Q3", "Q4"]


@dataclass(slots=True)
class QClassification:
    """One sentence's Q matrix verdict.

    Attributes
    ----------
    quadrant:
        Q1 (episodic, non-affective) — event log
        Q2 (episodic, affective)     — autonoetic memory
        Q3 (semantic, non-affective) — fact
        Q4 (semantic, affective)     — opinion / value judgment
    episodic_score, affective_score:
        Both in [0, 1].
    confidence:
        How far the verdict is from the *worst* axis threshold. 0 = on
        the line, 1 = at the corner. Callers can route low-confidence
        sentences to a Tier-2 LLM fallback.
    signals:
        Dict of contributing signal counts for debugging / paper data.
    """

    quadrant: Quadrant
    episodic_score: float
    affective_score: float
    confidence: float
    signals: dict[str, int]

    @property
    def short_label(self) -> str:
        """Single-character marker code: E / M / F / O.

        Used in TurnBox marker rendering — cue richness without bloating
        the line length.
        """
        return {"Q1": "E", "Q2": "M", "Q3": "F", "Q4": "O"}[self.quadrant]


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


# Sentence boundary regex — mirrors summarizer._SENT_SPLIT.
_SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+|\n{2,}")


class QMatrixClassifier:
    """Deterministic episodic × affective classifier.

    Parameters
    ----------
    episodic_threshold, affective_threshold:
        Both in [0, 1]. A signal score at-or-above its threshold is
        considered "high". Defaults to 0.30 — chosen empirically so
        that a single strong cue (first-person + past tense, or
        polarity word + intensifier) tips a short sentence into the
        high zone. Tune per-corpus when paper bench runs are in.
    """

    def __init__(
        self,
        *,
        episodic_threshold: float = 0.30,
        affective_threshold: float = 0.30,
    ):
        if not 0.0 <= episodic_threshold <= 1.0:
            raise ValueError("episodic_threshold must be in [0, 1]")
        if not 0.0 <= affective_threshold <= 1.0:
            raise ValueError("affective_threshold must be in [0, 1]")
        self.ep_th = episodic_threshold
        self.af_th = affective_threshold

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def classify(self, sentence: str) -> QClassification:
        """Classify a single sentence."""
        text = (sentence or "").strip()
        if not text:
            return QClassification(
                quadrant="Q3",
                episodic_score=0.0,
                affective_score=0.0,
                confidence=0.0,
                signals={},
            )

        tokens = _tokenize(text)
        n = max(1, len(tokens))

        ep_signals = self._episodic_signals(text, tokens)
        af_signals = self._affective_signals(text, tokens)

        # Episodic axis — weight signals by reliability.
        # first_person is *weak* alone ("I am a developer" has
        # first_person but is semantic, not episodic).
        # Strong signals: memory verbs, sensory verbs, TR
        # personal / possessive suffixes, temporal / spatial anchors,
        # English past-tense forms.
        ep_strong = (
            ep_signals["memory_verb"]
            + ep_signals["sensory_verb"]
            + ep_signals["tr_personal_suffix"]
            + ep_signals["tr_possessive_suffix"]
            + ep_signals["temporal_anchor"]
            + ep_signals["spatial_anchor"]
            + ep_signals["en_past_form"]
        )
        ep_weak = ep_signals["first_person"]
        ep_raw = ep_strong + ep_weak * 0.5
        af_raw = sum(af_signals.values())

        ep_score = min(1.0, ep_raw / max(3.0, n * 0.25))
        af_score = min(1.0, af_raw / max(3.0, n * 0.25))

        # Quadrant from the two axes.
        ep_high = ep_score >= self.ep_th
        af_high = af_score >= self.af_th
        quadrant: Quadrant
        if ep_high and not af_high:
            quadrant = "Q1"
        elif ep_high and af_high:
            quadrant = "Q2"
        elif not ep_high and af_high:
            quadrant = "Q4"
        else:
            quadrant = "Q3"

        # Confidence = distance from the worst-violated threshold.
        # If both axes are far from the line → high confidence.
        d_ep = abs(ep_score - self.ep_th)
        d_af = abs(af_score - self.af_th)
        confidence = min(1.0, min(d_ep, d_af) * 2.0)

        merged = {**{f"ep_{k}": v for k, v in ep_signals.items()},
                  **{f"af_{k}": v for k, v in af_signals.items()}}

        return QClassification(
            quadrant=quadrant,
            episodic_score=ep_score,
            affective_score=af_score,
            confidence=confidence,
            signals=merged,
        )

    def classify_text(self, text: str) -> list[QClassification]:
        """Split into sentences and classify each."""
        sentences = [s for s in _SENT_SPLIT.split(text or "") if s.strip()]
        return [self.classify(s) for s in sentences]

    def classify_text_pairs(
        self, text: str
    ) -> list[tuple[str, QClassification]]:
        """Split + classify, returning ``(sentence, verdict)`` pairs.

        Useful when the caller needs the original sentence string
        alongside its classification (e.g. Q3 dedup must hash the
        sentence text, not just the quadrant label).
        """
        sentences = [s for s in _SENT_SPLIT.split(text or "") if s.strip()]
        return [(s, self.classify(s)) for s in sentences]

    def classify_many(
        self, sentences: Sequence[str]
    ) -> list[QClassification]:
        """Convenience batch — already-segmented input."""
        return [self.classify(s) for s in sentences]

    # ------------------------------------------------------------------
    # Signal helpers
    # ------------------------------------------------------------------
    def _episodic_signals(
        self, text: str, tokens: list[str]
    ) -> dict[str, int]:
        sig: dict[str, int] = {}

        # If the sentence is clearly an opinion ("I think…", "I believe…"),
        # first-person pronouns are part of the opinion frame, not of
        # episodic recall. Suppress the first_person episodic signal in
        # that case so the verdict can land in Q4.
        opinion_hits = sum(1 for t in tokens if t in _OPINION_VERBS)
        raw_first_person = sum(1 for t in tokens if t in _FIRST_PERSON)
        sig["first_person"] = 0 if opinion_hits > 0 else raw_first_person

        sig["memory_verb"] = sum(1 for t in tokens if t in _MEMORY_VERBS)
        sig["sensory_verb"] = sum(1 for t in tokens if t in _SENSORY_VERBS)
        sig["en_past_form"] = sum(1 for t in tokens if t in _EN_PAST_FORMS)
        # Note: a separate `\w+ed\b` past-suffix counter was tried and
        # removed — too many false positives on "need", "indeed",
        # "seed", etc. The explicit _EN_PAST_FORMS set is enough.
        # TR personal suffixes — strong signal by themselves.
        sig["tr_personal_suffix"] = len(
            _TR_PERSONAL_SUFFIX.findall(text)
        )
        # TR possessive suffixes — "eşim", "evim", "annem".
        # Discount overlap with personal-verb suffixes by capping the
        # additional count when the personal-suffix signal already
        # registered.
        possessive_hits = len(_TR_POSSESSIVE_SUFFIX.findall(text))
        sig["tr_possessive_suffix"] = max(
            0, possessive_hits - sig["tr_personal_suffix"]
        )
        sig["temporal_anchor"] = len(_TEMPORAL_ANCHOR_RE.findall(text))
        sig["spatial_anchor"] = len(_SPATIAL_ANCHOR_RE.findall(text))
        return sig

    def _affective_signals(
        self, text: str, tokens: list[str]
    ) -> dict[str, int]:
        lowered = text.lower()
        sig: dict[str, int] = {}

        sig["pos_word"] = sum(1 for t in tokens if t in _POS_WORDS)
        sig["neg_word"] = sum(1 for t in tokens if t in _NEG_WORDS)
        sig["emotion_word"] = sum(1 for t in tokens if t in _EMOTION_WORDS)
        sig["intensifier"] = sum(1 for t in tokens if t in _INTENSIFIERS)
        sig["subj_phrase"] = sum(
            1 for phrase in _SUBJECTIVITY_PHRASES if phrase in lowered
        )
        sig["exclamation"] = text.count("!")
        sig["value_statement"] = len(_VALUE_STATEMENT_RE.findall(text))
        # Opinion verbs (think/believe/suppose) carry subjectivity weight
        # without being episodic recall.
        sig["opinion_verb"] = sum(1 for t in tokens if t in _OPINION_VERBS)
        # Memory verbs (remember/forget/miss/özlüyorum) ALSO contribute
        # to affective — remembering and forgetting are emotionally
        # weighted by definition (Tulving's autonoetic awareness).
        sig["memory_verb_bonus"] = sum(
            1 for t in tokens if t in _MEMORY_VERBS
        )
        return sig


# ---------------------------------------------------------------------------
# Module-level convenience (lazy singleton)
# ---------------------------------------------------------------------------


_DEFAULT_CLASSIFIER: QMatrixClassifier | None = None


def classify_sentence(sentence: str) -> QClassification:
    """Quick one-off using the default classifier."""
    global _DEFAULT_CLASSIFIER
    if _DEFAULT_CLASSIFIER is None:
        _DEFAULT_CLASSIFIER = QMatrixClassifier()
    return _DEFAULT_CLASSIFIER.classify(sentence)


def classify_text(text: str) -> list[QClassification]:
    """Split & classify with the default classifier."""
    global _DEFAULT_CLASSIFIER
    if _DEFAULT_CLASSIFIER is None:
        _DEFAULT_CLASSIFIER = QMatrixClassifier()
    return _DEFAULT_CLASSIFIER.classify_text(text)
