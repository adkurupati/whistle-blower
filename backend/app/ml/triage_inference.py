"""
Inference wrapper for the Phase 6 triage classifier.

Turns a raw comment string into (probability, decision) using the trained
weights at `triage_classifier_weights.pt` and the same
sentence-transformer embedding model the training set was built on
(`all-MiniLM-L6-v2`). Model + embedder are loaded once, lazily, on first
call and reused — hot-path calls skip all the loading cost.

Intended callers: Phase 7 retrieval / synthesis code that wants to cheaply
skip comments before spending an LLM call, and any future ingestion-time
filtering.

Operating threshold: 0.4 (recall-prioritized). Rationale — a missed
controversy fails silently at triage (never reaches the LLM, so we can't
correct it downstream), whereas a false positive just costs one LLM
inference. On the held-out test set this gives recall 0.939 while still
dropping ~91% of the input (193 of 2213 rows kept). Not final — revisit
once Phase 7 knows real LLM cost per comment.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import torch

from app.ml.triage_classifier import WEIGHTS_PATH, TriageClassifier


EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"
DEFAULT_THRESHOLD = 0.4


@dataclass(frozen=True)
class TriageResult:
    probability: float  # sigmoid(logit), 0..1
    is_relevant: bool   # probability >= threshold


@lru_cache(maxsize=1)
def _load_embedder():
    # Imported lazily so `import triage_inference` doesn't pull the ~90 MB
    # sentence-transformers download path at import time for callers that
    # only need the class definition.
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(EMBEDDING_MODEL_NAME)


@lru_cache(maxsize=1)
def _load_model(weights_path: str = str(WEIGHTS_PATH)) -> TriageClassifier:
    model = TriageClassifier()
    model.load_state_dict(torch.load(weights_path, map_location="cpu"))
    model.eval()
    return model


def classify_comment(
    text: str,
    threshold: float = DEFAULT_THRESHOLD,
) -> TriageResult:
    """Score one comment. See classify_comments() for the batched form."""
    return classify_comments([text], threshold=threshold)[0]


def classify_comments(
    texts: list[str],
    threshold: float = DEFAULT_THRESHOLD,
    batch_size: int = 64,
) -> list[TriageResult]:
    """Batched inference — pass all your comments at once when possible;
    embedding is the expensive step and batches amortize it."""
    if not texts:
        return []

    embedder = _load_embedder()
    model = _load_model()

    embeddings = embedder.encode(
        texts,
        batch_size=batch_size,
        show_progress_bar=False,
        convert_to_numpy=True,
        normalize_embeddings=False,
    ).astype(np.float32)

    with torch.no_grad():
        logits = model(torch.from_numpy(embeddings))
        probs = torch.sigmoid(logits).cpu().numpy()

    return [
        TriageResult(probability=float(p), is_relevant=bool(p >= threshold))
        for p in probs
    ]
