"""Epistemic markers — patent Talep 3 implementasyonu.

Bilginin güvenilirlik boyutunu sınıflandırır. Q matrix'ten ortogonal:

- Q matrix → bilginin **türü** (episodic/semantic × affective/non-affective)
- Epistemic → bilginin **güvenilirliği** (verified/hearsay/corrected/contradicted/uncertain)

Beş sınıf (kısaltma — patent claim ek başvurusu için 2-harf kanonik):

    VR  verified    default. First-person observation veya citation.
                    Cümle başında özel tetikleyici yoksa bu sınıfa düşer.
    HR  hearsay     "I heard", "apparently", "duyduğuma göre", ...
    CR  corrected   "actually", "wait", "düzeltiyorum", "pardon", ...
    CD  contradicted önceki bir claim'in tersine bir ifade. V1'de
                    sadece trigger-based; cross-turn semantic similarity
                    sonraki iterasyon (semantic_store entegrasyonu).
    UC  uncertain   "maybe", "I think", "sanırım", "galiba", ...

**Tag formatında default VR gösterilmez** (Seçenek B): sadece sapmalar
tag attribute alır. Örnek:

    <!--Q3-->Paris is the capital of France.<!--/Q3-->                 (VR, tag-siz)
    <!--Q3 HR-->I heard Paris has the best croissants.<!--/Q3-->
    <!--Q4 CR-->Actually, I think Italy is better.<!--/Q4-->
    <!--Q4 UC-->Maybe Rome is comparable.<!--/Q4-->

Patent referansları:
- compresh-ltd/legal/patents/provisional-uk/claims-draft-v3.md (Talep 3)
- wiki-comp/decisions/2026-05-12-epistemic-markers.md (bu dosya)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Sequence


class EpistemicMarker(Enum):
    """Epistemic güvenilirlik sınıfları."""
    VR = "verified"
    HR = "hearsay"
    CR = "corrected"
    CD = "contradicted"
    UC = "uncertain"

    @property
    def short(self) -> str:
        """İki harfli kısaltma — tag formatı için."""
        return self.name

    @property
    def is_default(self) -> bool:
        """Default (VR) ise tag'de gösterilmez."""
        return self is EpistemicMarker.VR


@dataclass(slots=True)
class EpistemicVerdict:
    """Bir cümlenin epistemic sınıflandırma sonucu."""
    marker: EpistemicMarker
    trigger: Optional[str] = None  # hangi ifade tetikledi (debug/training için)


# ---------------------------------------------------------------------------
# Trigger lists — patent Talep 3'ten + Türkçe paralel
# ---------------------------------------------------------------------------

HEARSAY_TRIGGERS: tuple[str, ...] = (
    # English
    "i heard", "they say", "they said", "people say", "apparently",
    "supposedly", "allegedly", "rumor has it", "i've been told",
    "word is", "the story goes", "it is said", "reportedly",
    # Turkish
    "duyduğuma göre", "diyorlar ki", "söylendiğine göre",
    "rivayete göre", "güya", "sözde", "kulağıma çalındı",
)

CORRECTED_TRIGGERS: tuple[str, ...] = (
    # English — leading position usually
    "actually,", "actually ", "wait,", "wait —", "wait...",
    "no, i mean", "let me correct", "let me clarify", "i meant",
    "pardon,", "scratch that", "on second thought",
    "correction:", "to clarify,",
    # Turkish
    "pardon", "düzeltiyorum", "aslında,", "aslında ", "yanlış söyledim",
    "demek istediğim", "şöyle düzelteyim", "şunu düzeltmeliyim",
)

UNCERTAIN_TRIGGERS: tuple[str, ...] = (
    # English
    "maybe", "perhaps", "i think", "i suppose", "i guess",
    "possibly", "probably", "might be", "could be", "i'm not sure",
    "in my opinion", "if i recall", "i believe",
    # Turkish
    "sanırım", "galiba", "belki", "muhtemelen", "bence",
    "öyle hatırlıyorum", "olabilir", "tahminim", "kanımca",
)


# ---------------------------------------------------------------------------
# Sentence splitter (q_matrix._SENT_SPLIT ile uyumlu)
# ---------------------------------------------------------------------------

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


class EpistemicClassifier:
    """Rule-based epistemic marker classifier — V1 implementation.

    V1: trigger phrase detection (lowercase substring match).
    V2 (gelecek): CD (contradicted) için cross-turn semantic similarity
    (semantic_store ile entegrasyon — Q3 dedup'ın benzeri).

    Sınıflandırma önceliği (en spesifik → en genel):
      1. CR (corrected) — explicit walkback
      2. HR (hearsay)   — attribution to third party
      3. UC (uncertain) — speaker hedging
      4. VR (verified)  — default
    """

    def __init__(
        self,
        hearsay_triggers: Sequence[str] = HEARSAY_TRIGGERS,
        corrected_triggers: Sequence[str] = CORRECTED_TRIGGERS,
        uncertain_triggers: Sequence[str] = UNCERTAIN_TRIGGERS,
    ):
        self.hearsay_triggers = tuple(hearsay_triggers)
        self.corrected_triggers = tuple(corrected_triggers)
        self.uncertain_triggers = tuple(uncertain_triggers)

    def classify(self, sentence: str) -> EpistemicVerdict:
        """Bir cümleyi sınıflandır."""
        if not sentence or not sentence.strip():
            return EpistemicVerdict(EpistemicMarker.VR)

        text = sentence.lower()

        # CR > HR > UC > VR (priority chain)
        for trig in self.corrected_triggers:
            if trig in text:
                return EpistemicVerdict(EpistemicMarker.CR, trigger=trig)
        for trig in self.hearsay_triggers:
            if trig in text:
                return EpistemicVerdict(EpistemicMarker.HR, trigger=trig)
        for trig in self.uncertain_triggers:
            if trig in text:
                return EpistemicVerdict(EpistemicMarker.UC, trigger=trig)

        return EpistemicVerdict(EpistemicMarker.VR)

    def classify_text_pairs(
        self, text: str,
    ) -> list[tuple[str, EpistemicVerdict]]:
        """Metni cümlelere böl, her cümleyi sınıflandır.

        Returns: ``[(sentence, verdict), ...]``
        """
        sentences = [s for s in _SENT_SPLIT.split(text or "") if s.strip()]
        return [(s, self.classify(s)) for s in sentences]


# ---------------------------------------------------------------------------
# Convenience module-level function
# ---------------------------------------------------------------------------


_DEFAULT_CLASSIFIER: Optional[EpistemicClassifier] = None


def classify_sentence(sentence: str) -> EpistemicVerdict:
    """Module-level convenience using a shared classifier instance."""
    global _DEFAULT_CLASSIFIER
    if _DEFAULT_CLASSIFIER is None:
        _DEFAULT_CLASSIFIER = EpistemicClassifier()
    return _DEFAULT_CLASSIFIER.classify(sentence)
