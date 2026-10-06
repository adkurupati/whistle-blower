"""
Index every social_discussion row into Qdrant for Phase 7 AI Verdict retrieval.

Flow:
  1. Create the `social_discussion` collection (384-dim cosine) + payload
     indexes on game_id and is_relevant if they don't exist.
  2. Pull every social_discussion row from Postgres in batches, ordered by id.
  3. Skip ids that already have a point in Qdrant unless --force is set.
  4. Embed comment_text with all-MiniLM-L6-v2 (same model the triage classifier
     uses) and score with the triage classifier from the same embeddings — one
     embed pass per comment, not two.
  5. Upsert to Qdrant with the point id = social_discussion.id (so each row and
     its vector stay 1:1, per the spec).

ALL rows are indexed, including legacy rows with published_at = NULL. The
trigger gate separately filters on published_at — the indexer doesn't.

Run from backend/:
    python scripts/index_social_discussion.py
    python scripts/index_social_discussion.py --force
    python scripts/index_social_discussion.py --batch 128
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qdrant_client.http import models as qm
from sqlalchemy import func, select

from app.db import SessionLocal
from app.ml.triage_inference import (
    DEFAULT_THRESHOLD,
    classify_from_embeddings,
    embed_comments,
)
from app.models import SocialDiscussion
from app.retrieval.qdrant import COLLECTION, ensure_collection, get_client


def existing_point_ids(client, batch: int = 5000) -> set[int]:
    """Scroll the whole collection once and return the set of ids present.
    ~10k rows now; a single-pass scroll is fine. If this corpus grows to
    where that stops being true, switch to per-batch `retrieve(ids=...)`."""
    ids: set[int] = set()
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=COLLECTION,
            with_payload=False,
            with_vectors=False,
            limit=batch,
            offset=offset,
        )
        ids.update(int(p.id) for p in points)
        if offset is None:
            break
    return ids


def _to_payload(row: SocialDiscussion, prob: float, threshold: float) -> dict:
    """Dict payload for one point. All fields nullable except is_relevant."""
    published_at = (
        row.published_at.isoformat() if row.published_at is not None else None
    )
    return {
        "game_id": row.game_id,
        "video_id": row.video_id,
        "source_channel": row.source_channel,
        "published_at": published_at,
        "engagement_score": row.engagement_score,
        "retrieval_query_template": row.retrieval_query_template,
        "triage_prob": float(prob),
        "is_relevant": bool(prob >= threshold),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true",
                    help="re-embed and re-upsert rows that are already indexed")
    ap.add_argument("--batch", type=int, default=128,
                    help="comments per embed/upsert batch (default: 128)")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                    help=f"triage threshold stored as `is_relevant` "
                         f"(default: {DEFAULT_THRESHOLD})")
    args = ap.parse_args()

    client = get_client()
    ensure_collection(client)

    t_start = time.time()

    with SessionLocal() as session:
        total_rows = session.execute(
            select(func.count(SocialDiscussion.id))
        ).scalar() or 0

        skip_ids: set[int] = set()
        if not args.force:
            skip_ids = existing_point_ids(client)
            print(f"found {len(skip_ids)} point(s) already in Qdrant — "
                  f"skipping those", flush=True)

        print(f"total social_discussion rows: {total_rows}  "
              f"batch={args.batch}  threshold={args.threshold}  "
              f"force={args.force}", flush=True)

        indexed = 0
        skipped = 0

        # Stream rows in id order via LIMIT/OFFSET-style windowing on the PK
        # (keyset pagination), so we don't materialize the whole table at once.
        last_id = 0
        while True:
            rows = session.execute(
                select(SocialDiscussion)
                .where(SocialDiscussion.id > last_id)
                .order_by(SocialDiscussion.id)
                .limit(args.batch)
            ).scalars().all()
            if not rows:
                break
            last_id = rows[-1].id

            to_index = [r for r in rows if r.id not in skip_ids]
            skipped += len(rows) - len(to_index)
            if not to_index:
                continue

            texts = [r.comment_text for r in to_index]
            embeddings = embed_comments(texts, batch_size=args.batch)
            scores = classify_from_embeddings(embeddings, threshold=args.threshold)

            points = []
            for r, emb, s in zip(to_index, embeddings, scores):
                points.append(qm.PointStruct(
                    id=int(r.id),
                    vector=emb.tolist(),
                    payload=_to_payload(r, s.probability, args.threshold),
                ))
            client.upsert(collection_name=COLLECTION, points=points, wait=True)
            indexed += len(points)

            elapsed = time.time() - t_start
            print(f"  indexed={indexed:>5}  skipped={skipped:>5}  "
                  f"last_id={last_id:>6}  elapsed={elapsed:>5.1f}s", flush=True)

    final_count = client.count(collection_name=COLLECTION, exact=True).count
    elapsed = time.time() - t_start
    print()
    print("=" * 72)
    print(f"indexed={indexed}  already_present_skipped={skipped}  "
          f"total_in_qdrant={final_count}  elapsed={elapsed:.1f}s")
    print("=" * 72)
    if final_count != total_rows:
        print(f"NOTE: Qdrant point count ({final_count}) != "
              f"social_discussion rows ({total_rows}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
