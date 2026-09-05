"""
Build the Phase 6 triage-classifier training dataset from `social_discussion`.

Pipeline:
  1. Extract only recap-template rows (the "decontaminated slice" per today's
     confound analysis — non-recap-template rows are auto-positive by
     construction and would let the classifier cheat on template provenance
     rather than learn from comment language).
  2. Label each row via label_social_discussion.py's rule: 1 if the keyword
     regex or an official-name match fires, else 0. Does NOT expose those
     boolean flags to the model — embedding of raw comment_text only.
  3. Embed comment_text with sentence-transformers all-MiniLM-L6-v2
     (384-dim, CPU-friendly, no API key).
  4. Split by game_id, not by row — the test games below are held out
     entirely so evaluation measures generalization to a genuinely unseen
     incident, not a random slice of comments from games the model has
     already seen the fanbase discussing.
  5. Cache everything to a single .npz for the next step (actual model
     training) to reload without re-embedding.

Holdout (2 controversial + 1 clean + 1 jan2026, positives on both sides
sanity-checked to be within the same ~4-5% range before running):
    0022400230  TOR @ BOS (controversial, 4 IC/INC)
    0022400205  CHA @ PHI (controversial, 2 IC/INC)
    0022400020  MIN @ SAC (clean pool)
    0022500696  GSW @ DET (jan2026, Draymond/JT Orr incident — the natural
                "unseen controversial moment" test case)

Run from backend/:
    python scripts/build_classifier_dataset.py

Output:
    backend/data/classifier_dataset.npz  (embeddings, labels, is_train,
                                          game_ids, comment_ids)
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.db import SessionLocal
from app.models import SocialDiscussion
from scripts.label_social_discussion import (
    KEYWORDS_RE,
    is_recap_template,
    load_officials_by_game,
)

# Stratified holdout — 2 controversial pool + 1 clean pool + 1 jan2026 batch.
# Picked so test-side positive count ≈ 99 (well above the ~50 minimum for
# meaningful F1) and test-side positive rate ≈ train-side positive rate
# (no distributional skew introduced by the split).
HOLDOUT_GAME_IDS = frozenset({
    "0022400230",  # TOR @ BOS  (controversial, 4 IC/INC)
    "0022400205",  # CHA @ PHI  (controversial, 2 IC/INC)
    "0022400020",  # MIN @ SAC  (clean)
    "0022500696",  # GSW @ DET  (jan2026, Draymond/JT Orr incident)
})

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "classifier_dataset.npz"

EMBEDDING_MODEL = "all-MiniLM-L6-v2"  # 384-dim, ~90 MB download on first use
EMBEDDING_BATCH_SIZE = 64             # comfortable on CPU


# ---------- label logic (reuses label_social_discussion.py's regex + officials) ----------

def compute_label(text: str, game_id: str, officials_by_game: dict) -> int:
    """1 if keyword match or official-name match fires, else 0."""
    if KEYWORDS_RE.search(text or ""):
        return 1
    text_lower = (text or "").lower()
    for name in officials_by_game.get(game_id, ()):
        if name.lower() in text_lower:
            return 1
    return 0


# ---------- data extraction ----------

def load_recap_slice(session):
    """Return recap-template rows only, ordered by id for reproducibility."""
    officials = load_officials_by_game(session)
    all_rows = list(
        session.execute(select(SocialDiscussion).order_by(SocialDiscussion.id)).scalars()
    )
    kept = [r for r in all_rows if is_recap_template(r.retrieval_query_template)]
    return kept, officials


# ---------- embedding ----------

def embed_texts(texts: list[str]) -> np.ndarray:
    """Batch-embed with all-MiniLM-L6-v2. Returns (N, 384) float32."""
    # Silence HF progress noise other than our own timing log.
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    from sentence_transformers import SentenceTransformer

    print(f"Loading {EMBEDDING_MODEL} (first run downloads ~90 MB) ...", flush=True)
    t0 = time.time()
    model = SentenceTransformer(EMBEDDING_MODEL)
    print(f"  loaded in {time.time() - t0:.1f}s", flush=True)

    print(f"Embedding {len(texts)} comments (batch={EMBEDDING_BATCH_SIZE}) ...",
          flush=True)
    t0 = time.time()
    emb = model.encode(
        texts,
        batch_size=EMBEDDING_BATCH_SIZE,
        show_progress_bar=True,
        convert_to_numpy=True,
        normalize_embeddings=False,
    ).astype(np.float32)
    print(f"  embedded in {time.time() - t0:.1f}s  shape={emb.shape}", flush=True)
    return emb


# ---------- main ----------

def main() -> int:
    with SessionLocal() as session:
        rows, officials = load_recap_slice(session)

    if not rows:
        print("No recap-template rows found — nothing to build.")
        return 1

    print(f"Recap-slice rows loaded: {len(rows)}")

    # Extract in one pass so index alignment across arrays is exact.
    texts = [r.comment_text or "" for r in rows]
    labels = np.array(
        [compute_label(r.comment_text or "", r.game_id, officials) for r in rows],
        dtype=np.int8,
    )
    game_ids = np.array([r.game_id for r in rows], dtype=object)
    comment_ids = np.array([r.source_item_id for r in rows], dtype=object)
    templates = np.array(
        [r.retrieval_query_template for r in rows], dtype=object
    )

    # By-game split — holdout games go to test, everything else to train.
    is_train = np.array([g not in HOLDOUT_GAME_IDS for g in game_ids], dtype=bool)

    train_games = set(game_ids[is_train])
    test_games = set(game_ids[~is_train])
    overlap = train_games & test_games
    assert not overlap, f"Game overlap between train and test: {overlap}"

    # Embed all rows at once — the split masks decide which are train/test at
    # load time, but we keep everything aligned in one matrix so downstream
    # code doesn't have to re-embed if the split changes.
    embeddings = embed_texts(texts)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        OUTPUT_PATH,
        embeddings=embeddings,
        labels=labels,
        is_train=is_train,
        game_ids=game_ids,
        comment_ids=comment_ids,
        retrieval_query_templates=templates,
    )

    # ---------- report ----------
    n = len(labels)
    n_train = int(is_train.sum())
    n_test = int((~is_train).sum())
    pos_train = int(labels[is_train].sum())
    pos_test = int(labels[~is_train].sum())

    print("\n" + "=" * 72)
    print(f"DATASET SAVED  {OUTPUT_PATH}  ({OUTPUT_PATH.stat().st_size/1024:.0f} KB)")
    print("=" * 72)
    print(f"  embedding dim:  {embeddings.shape[1]}")
    print(f"  total rows:     {n}")
    print(f"  positives:      {int(labels.sum())} ({100*labels.mean():.2f}%)")
    print()
    print(f"  train: {n_train:>5} rows  {pos_train:>4} pos "
          f"({100*pos_train/n_train:.2f}%)  {n_train - pos_train:>5} neg")
    print(f"  test:  {n_test:>5} rows  {pos_test:>4} pos "
          f"({100*pos_test/n_test:.2f}%)  {n_test - pos_test:>5} neg")
    print()
    print(f"  train games ({len(train_games)}): {sorted(train_games)}")
    print(f"  test  games ({len(test_games)}): {sorted(test_games)}")
    print(f"  game overlap:   {len(overlap)}  (asserted zero)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
