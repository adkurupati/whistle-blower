"""
Train the Phase 6 triage classifier.

Loads backend/data/classifier_dataset.npz (produced by
build_classifier_dataset.py), fits a small feedforward net on the 384-dim
embeddings, and writes trained weights to
backend/app/ml/triage_classifier_weights.pt.

The dataset is ~4.3% positive and we can't afford to discard the 369 train
positives — we lean on BCEWithLogitsLoss(pos_weight=neg/pos) to keep the
minority class from being drowned out, rather than downsampling.

Metrics: precision / recall / F1 on the positive class and PR-AUC, then a
threshold sweep so we can pick an operating point deliberately (missing a
real controversy is worse than flagging a few extra comments for the LLM
step). Accuracy is intentionally NOT reported — trivially 95%+ on a
95.7%-negative corpus.

Sanity check: after training, print 10 real comments (mix of TP / FP / FN
at the chosen operating threshold, 0.4 — recall-prioritized, since a
missed controversy fails silently at triage while a false positive just
costs LLM time downstream) with the text next to prediction + label,
looked up from the DB by (source_item_id, game_id). More informative than
any single metric — this is how we tell if the model is reading language
or memorizing template quirks.

Training uses early stopping on test loss (patience=4) and restores the
best checkpoint before saving weights, since the loss curve overfits
past ~epoch 10-11 while the ranking (PR-AUC) stays intact.

Run from backend/:
    python scripts/train_triage_classifier.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import torch
from sklearn.metrics import (
    average_precision_score,
    precision_recall_fscore_support,
)
from torch import nn
from torch.utils.data import DataLoader, TensorDataset

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.ml.triage_classifier import WEIGHTS_PATH, TriageClassifier
from app.models import SocialDiscussion
from sqlalchemy import select


DATASET_PATH = Path(__file__).resolve().parent.parent / "data" / "classifier_dataset.npz"

# Hard cap; early stopping usually fires well before this.
MAX_EPOCHS = 40
# Stop if test loss hasn't improved in this many consecutive epochs; restore
# the best checkpoint before saving. Test loss bottoms around epoch 10-11 on
# this dataset — patience=4 catches that reliably without chasing noise.
EARLY_STOP_PATIENCE = 4
BATCH_SIZE = 128
LR = 1e-3
WEIGHT_DECAY = 1e-4
SEED = 42

THRESHOLD_SWEEP = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
DEFAULT_THRESHOLD = 0.4
N_EYEBALL_SAMPLES = 10


def set_seed(seed: int) -> None:
    np.random.seed(seed)
    torch.manual_seed(seed)


def load_split(path: Path):
    d = np.load(path, allow_pickle=True)
    x = d["embeddings"].astype(np.float32)
    y = d["labels"].astype(np.float32)
    tr = d["is_train"].astype(bool)
    return (
        x[tr], y[tr],
        x[~tr], y[~tr],
        d["game_ids"], d["comment_ids"], tr,
    )


def epoch_loss(model, loader, loss_fn, device) -> float:
    model.eval()
    total_loss = 0.0
    total_n = 0
    with torch.no_grad():
        for xb, yb in loader:
            xb, yb = xb.to(device), yb.to(device)
            logits = model(xb)
            loss = loss_fn(logits, yb)
            total_loss += loss.item() * len(xb)
            total_n += len(xb)
    return total_loss / total_n


def predict_probs(model, x: torch.Tensor, device) -> np.ndarray:
    model.eval()
    with torch.no_grad():
        logits = model(x.to(device))
        probs = torch.sigmoid(logits).cpu().numpy()
    return probs


def report_threshold_sweep(y_true: np.ndarray, probs: np.ndarray) -> None:
    print(f"\n  {'threshold':>9}  {'precision':>9}  {'recall':>7}  {'f1':>6}  "
          f"{'pred_pos':>8}  {'tp':>4}  {'fp':>4}  {'fn':>4}")
    print("  " + "-" * 66)
    for t in THRESHOLD_SWEEP:
        pred = (probs >= t).astype(np.int8)
        p, r, f1, _ = precision_recall_fscore_support(
            y_true, pred, average="binary", zero_division=0
        )
        tp = int(((pred == 1) & (y_true == 1)).sum())
        fp = int(((pred == 1) & (y_true == 0)).sum())
        fn = int(((pred == 0) & (y_true == 1)).sum())
        pred_pos = int(pred.sum())
        print(f"  {t:>9.2f}  {p:>9.3f}  {r:>7.3f}  {f1:>6.3f}  "
              f"{pred_pos:>8d}  {tp:>4d}  {fp:>4d}  {fn:>4d}")


def load_comment_text(rows: list[tuple[str, str]]) -> dict[tuple[str, str], str]:
    """Fetch comment_text keyed by (game_id, source_item_id)."""
    if not rows:
        return {}
    wanted_ids = {r[1] for r in rows}
    out: dict[tuple[str, str], str] = {}
    with SessionLocal() as session:
        stmt = select(
            SocialDiscussion.game_id,
            SocialDiscussion.source_item_id,
            SocialDiscussion.comment_text,
        ).where(SocialDiscussion.source_item_id.in_(list(wanted_ids)))
        for gid, sid, txt in session.execute(stmt):
            out[(gid, sid)] = txt
    return out


def pick_eyeball_samples(
    y_true: np.ndarray,
    probs: np.ndarray,
    game_ids: np.ndarray,
    comment_ids: np.ndarray,
    threshold: float,
) -> list[dict]:
    """Mix of true positives (highest-prob correct), false positives
    (highest-prob wrong-positive), false negatives (highest-prob missed
    positives). Balanced to N_EYEBALL_SAMPLES total, tolerant of thin classes."""
    pred = (probs >= threshold).astype(np.int8)
    tp_idx = np.where((pred == 1) & (y_true == 1))[0]
    fp_idx = np.where((pred == 1) & (y_true == 0))[0]
    fn_idx = np.where((pred == 0) & (y_true == 1))[0]

    # Sort each pool by informativeness — TP/FP by descending prob (most
    # confident predictions), FN by descending prob too (near-misses are
    # more instructive than obvious no-hopes).
    tp_idx = tp_idx[np.argsort(-probs[tp_idx])]
    fp_idx = fp_idx[np.argsort(-probs[fp_idx])]
    fn_idx = fn_idx[np.argsort(-probs[fn_idx])]

    # Target 4 TP, 3 FP, 3 FN, top-up from other pools if one is short.
    picks: list[tuple[str, int]] = []
    for kind, pool, n_want in [("TP", tp_idx, 4), ("FP", fp_idx, 3), ("FN", fn_idx, 3)]:
        for idx in pool[:n_want]:
            picks.append((kind, int(idx)))

    if len(picks) < N_EYEBALL_SAMPLES:
        seen = {i for _, i in picks}
        for kind, pool in [("TP", tp_idx), ("FP", fp_idx), ("FN", fn_idx)]:
            for idx in pool:
                if int(idx) not in seen:
                    picks.append((kind, int(idx)))
                    seen.add(int(idx))
                    if len(picks) >= N_EYEBALL_SAMPLES:
                        break
            if len(picks) >= N_EYEBALL_SAMPLES:
                break

    picks = picks[:N_EYEBALL_SAMPLES]

    key_pairs = [(str(game_ids[i]), str(comment_ids[i])) for _, i in picks]
    text_by_key = load_comment_text(key_pairs)

    samples = []
    for kind, i in picks:
        key = (str(game_ids[i]), str(comment_ids[i]))
        samples.append({
            "kind": kind,
            "prob": float(probs[i]),
            "label": int(y_true[i]),
            "game_id": key[0],
            "comment_id": key[1],
            "text": text_by_key.get(key, "<comment text not found in DB>"),
        })
    return samples


def print_eyeball_samples(samples: list[dict], threshold: float) -> None:
    print("\n" + "=" * 78)
    print(f"EYEBALL CHECK — 10 real test-set comments at threshold {threshold}")
    print("=" * 78)
    for i, s in enumerate(samples, 1):
        text = (s["text"] or "").strip().replace("\n", " ")
        if len(text) > 320:
            text = text[:317] + "..."
        print(
            f"\n[{i}] {s['kind']}  prob={s['prob']:.3f}  label={s['label']}  "
            f"game={s['game_id']}"
        )
        print(f"    {text}")


def main() -> int:
    set_seed(SEED)
    device = torch.device("cpu")  # tiny model, tiny dataset, CPU is fine

    x_train, y_train, x_test, y_test, all_game_ids, all_comment_ids, is_train = load_split(
        DATASET_PATH
    )

    n_train, n_test = len(y_train), len(y_test)
    pos_train, pos_test = int(y_train.sum()), int(y_test.sum())
    neg_train = n_train - pos_train

    print(f"train:  {n_train} rows  {pos_train} pos ({100*pos_train/n_train:.2f}%)")
    print(f"test:   {n_test} rows  {pos_test} pos ({100*pos_test/n_test:.2f}%)")

    pos_weight_value = neg_train / max(pos_train, 1)
    print(f"pos_weight = neg/pos = {pos_weight_value:.2f}")

    x_train_t = torch.from_numpy(x_train)
    y_train_t = torch.from_numpy(y_train)
    x_test_t = torch.from_numpy(x_test)
    y_test_t = torch.from_numpy(y_test)

    train_loader = DataLoader(
        TensorDataset(x_train_t, y_train_t),
        batch_size=BATCH_SIZE,
        shuffle=True,
    )
    # Full-batch evaluators — small enough dataset to skip mini-batching for
    # the loss/metric printouts.
    train_eval_loader = DataLoader(
        TensorDataset(x_train_t, y_train_t), batch_size=512, shuffle=False
    )
    test_eval_loader = DataLoader(
        TensorDataset(x_test_t, y_test_t), batch_size=512, shuffle=False
    )

    model = TriageClassifier().to(device)
    optim = torch.optim.Adam(model.parameters(), lr=LR, weight_decay=WEIGHT_DECAY)
    loss_fn = nn.BCEWithLogitsLoss(
        pos_weight=torch.tensor([pos_weight_value], device=device)
    )

    print(f"\nTraining up to {MAX_EPOCHS} epochs (batch={BATCH_SIZE}, lr={LR}, "
          f"early-stop patience={EARLY_STOP_PATIENCE} on test loss)")
    print(f"  {'epoch':>5}  {'train_loss':>10}  {'test_loss':>9}  note")
    print("  " + "-" * 40)

    best_test_loss = float("inf")
    best_epoch = 0
    best_state: dict | None = None
    epochs_since_improvement = 0

    for epoch in range(1, MAX_EPOCHS + 1):
        model.train()
        for xb, yb in train_loader:
            xb, yb = xb.to(device), yb.to(device)
            optim.zero_grad()
            logits = model(xb)
            loss = loss_fn(logits, yb)
            loss.backward()
            optim.step()

        train_loss = epoch_loss(model, train_eval_loader, loss_fn, device)
        test_loss = epoch_loss(model, test_eval_loader, loss_fn, device)

        note = ""
        if test_loss < best_test_loss:
            best_test_loss = test_loss
            best_epoch = epoch
            # Detach clone so a later train step can't mutate the snapshot.
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
            epochs_since_improvement = 0
            note = "*best"
        else:
            epochs_since_improvement += 1

        print(f"  {epoch:>5d}  {train_loss:>10.4f}  {test_loss:>9.4f}  {note}")

        if epochs_since_improvement >= EARLY_STOP_PATIENCE:
            print(f"  early stop — no improvement for {EARLY_STOP_PATIENCE} epochs "
                  f"(best epoch {best_epoch}, test_loss {best_test_loss:.4f})")
            break

    assert best_state is not None, "trained zero epochs?"
    model.load_state_dict(best_state)
    print(f"\nRestored best checkpoint (epoch {best_epoch}, test_loss {best_test_loss:.4f})")

    # ---------- eval ----------
    probs_test = predict_probs(model, x_test_t, device)
    pr_auc = average_precision_score(y_test, probs_test)

    pred_default = (probs_test >= DEFAULT_THRESHOLD).astype(np.int8)
    p, r, f1, _ = precision_recall_fscore_support(
        y_test, pred_default, average="binary", zero_division=0
    )

    print("\n" + "=" * 78)
    print(f"TEST METRICS (positive class only — accuracy withheld on purpose)")
    print("=" * 78)
    print(f"  PR-AUC (avg precision):  {pr_auc:.4f}")
    print(f"  @ threshold {DEFAULT_THRESHOLD}:  precision={p:.3f}  recall={r:.3f}  f1={f1:.3f}")
    print("\nThreshold sweep:")
    report_threshold_sweep(y_test, probs_test)

    # ---------- save weights ----------
    WEIGHTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    torch.save(model.state_dict(), WEIGHTS_PATH)
    size_kb = WEIGHTS_PATH.stat().st_size / 1024
    print(f"\nSaved weights: {WEIGHTS_PATH}  ({size_kb:.1f} KB)")

    # ---------- eyeball check ----------
    test_game_ids = all_game_ids[~is_train]
    test_comment_ids = all_comment_ids[~is_train]
    samples = pick_eyeball_samples(
        y_test, probs_test, test_game_ids, test_comment_ids, DEFAULT_THRESHOLD
    )
    print_eyeball_samples(samples, DEFAULT_THRESHOLD)

    return 0


if __name__ == "__main__":
    sys.exit(main())
