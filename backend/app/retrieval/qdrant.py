"""
Shared Qdrant client + collection constants for Phase 7 retrieval.

Collection `social_discussion`: 384-dim cosine. Point id = social_discussion.id
(the Postgres autoincrement PK). Payload mirrors the ingestion-time fields the
retrieval layer filters on; see scripts/index_social_discussion.py for the
exact shape.
"""

from __future__ import annotations

from functools import lru_cache

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from app.config import settings


COLLECTION = "social_discussion"
VECTOR_SIZE = 384  # all-MiniLM-L6-v2
DISTANCE = qm.Distance.COSINE


@lru_cache(maxsize=1)
def get_client() -> QdrantClient:
    return QdrantClient(url=settings.qdrant_url)


def ensure_collection(client: QdrantClient | None = None) -> None:
    """Create the collection + payload indexes if they don't exist. Safe to
    call on every run — no-op when things are already in place."""
    client = client or get_client()
    if not client.collection_exists(COLLECTION):
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=qm.VectorParams(size=VECTOR_SIZE, distance=DISTANCE),
        )
    # Payload indexes — idempotent via try/except on "already exists".
    # qdrant-client raises on duplicate index creation, so swallow those.
    for field, schema in (
        ("game_id", qm.PayloadSchemaType.KEYWORD),
        ("is_relevant", qm.PayloadSchemaType.BOOL),
    ):
        try:
            client.create_payload_index(
                collection_name=COLLECTION,
                field_name=field,
                field_schema=schema,
            )
        except Exception:  # noqa: BLE001 -- idempotency on re-run
            pass
