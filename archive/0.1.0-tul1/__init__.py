"""TUL 1.0 layer — Compresh proprietary classifiers above tulbase core.

Three components:

    - QMatrixClassifier: sentence-level Q1-Q4 categorization (Tulving
      episodic vs semantic taxonomy, x affective dimension)
    - EpistemicClassifier: VR/HR/CR/UC marker detection (verified,
      hearsay, corrected, uncertain)
    - SemanticStore: cross-turn Q3 (fact) sentence dedup via embeddings

These classifiers are injected into ``tulbase.Tier1Summarizer`` via the
``q_classifier`` and ``epi_classifier`` keyword arguments, enabling
Q-protective ranking when ``protect_mode != "off"``.

Patent: TR-TPMK 2026/007305 (Compresh Ltd, May 2026).
"""

from .epistemic import EpistemicClassifier
from .q_matrix import QClassification, QMatrixClassifier
from .semantic_store import SemanticStore
from .summarizer import Tier1Summarizer

__all__ = [
    "EpistemicClassifier",
    "QClassification",
    "QMatrixClassifier",
    "SemanticStore",
    "Tier1Summarizer",
]
