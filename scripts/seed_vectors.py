"""One-shot seed script: generate deterministic pseudo-embeddings for all
ready knowledge_chunks and upsert into Qdrant. Uses Chinese bigram overlap
so chunks sharing terminology get real cosine similarity — vector search
actually works for acceptance tests.

Run once inside app container:
  docker compose exec app python /app/scripts/seed_vectors.py
"""
import os, sys, math, hashlib
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
from app.config import Config
from app.db import get_conn, _rows
from app.qdrant_store import QdrantStore

DIM = Config.EMBEDDING_DIM or 1536
# Hash trick: map bigram → DIM index via SHA256, add small random-like weight
RNG_SEED = 42


def text_to_bigrams(text: str) -> list[str]:
    """Chinese bigrams + ascii word fragments (>=3 chars)."""
    text = text.lower()
    bigrams = []
    # Chinese characters: sliding bigram
    for i in range(len(text) - 1):
        a, b = text[i], text[i+1]
        if '\u4e00' <= a <= '\u9fff' or '\u4e00' <= b <= '\u9fff':
            bigrams.append(a + b)
    # ASCII words >= 3 chars
    import re
    for w in re.findall(r'[a-z]{3,}', text):
        bigrams.append(w)
    return bigrams


def hash_to_slot(bigram: str) -> tuple[int, float]:
    """Deterministic (slot_idx, weight) for a bigram."""
    h = hashlib.sha256(bigram.encode()).digest()
    slot = int.from_bytes(h[:6], 'big') % DIM
    # weight in [-1,1]
    w = (int.from_bytes(h[6:10], 'big') % 10000) / 10000.0 * 2.0 - 1.0
    return slot, w


def pseudo_embed(text: str) -> list[float]:
    vec = np.zeros(DIM, dtype=np.float64)
    for bg in text_to_bigrams(text):
        slot, w = hash_to_slot(bg)
        vec[slot] += w
    # L2 normalize
    norm = np.linalg.norm(vec)
    if norm < 1e-9:
        # zero vec — fill with deterministic noise
        rng = np.random.default_rng(hash(text) & 0xffffffff)
        vec = rng.standard_normal(DIM)
        norm = np.linalg.norm(vec)
    vec /= norm
    return vec.tolist()


def main():
    with get_conn() as conn:
        chunks = _rows(conn,
            "SELECT kc.id, kc.chunk_text, kc.class_id, kc.material_id, "
            "kc.knowledge_entry_id, kc.chunk_index "
            "FROM knowledge_chunks kc WHERE kc.index_status = 'ready' ",
        )
    print(f"[seed_vectors] {len(chunks)} ready chunks to embed (dim={DIM})")

    store = QdrantStore()
    store.ensure_collection()

    ids, vecs, payloads = [], [], []
    for c in chunks:
        ids.append(c["id"])
        vecs.append(pseudo_embed(c["chunk_text"]))
        payloads.append({
            "class_id": c["class_id"],
            "material_id": c["material_id"],
            "knowledge_entry_id": c["knowledge_entry_id"],
            "chunk_id": c["id"],
            "chunk_index": c["chunk_index"],
        })

    # Batch upsert in groups of 64
    BATCH = 64
    written = 0
    for i in range(0, len(ids), BATCH):
        store.upsert(ids[i:i+BATCH], vecs[i:i+BATCH], payloads[i:i+BATCH])
        written += BATCH
        print(f"[seed_vectors] upserted {min(written, len(ids))}/{len(ids)}")

    print(f"[seed_vectors] DONE. Total: {len(ids)} vectors written to Qdrant.")


if __name__ == "__main__":
    main()
