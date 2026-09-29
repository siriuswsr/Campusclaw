"""Post-upload indexing pipeline — split → embed → upsert Qdrant.

Call graph:
  materials.upload_api  →  post_upload()
  materials.reindex_api →  reindex()
  init_db.main          →  ensure_seed_indexes()

Spec constraints:
  - body_text (knowledge_entries) MUST remain original. Preprocessing is local
    to the chunker only; the original is never rewritten.
  - Embed failures: mark MySQL chunk as 'failed', DO NOT write partial vectors.
  - Qdrant failures: material/knowledge_entries stay; ALL chunks marked 'failed'.
    (We still call embed even if Qdrant is likely reachable — keeps logic simple.)
  - Qdrant payload MUST NOT contain chunk_text (only IDs + class_id).
  - Embed module MUST NOT be triggered by chunk module (separate functions here).
"""
import os
import traceback
from . import db
from .config import Config
from .chunker import chunk as _chunk
from .embed import EmbedClient
from .qdrant_store import QdrantStore


def post_upload(material_id: int, knowledge_entry_id: int, class_id: int,
                body_text: str, strategy: str | None = None) -> dict:
    """Run split → embed → Qdrant upsert for a freshly-uploaded material.
    Returns dict with chunk_ids list, n_ready, n_failed for callers that
    need visibility (loggers, tests).
    """
    out = {"material_id": material_id, "chunk_ids": [], "n_ready": 0, "n_failed": 0,
           "qdrant_written": False, "qdrant_error": None}

    # 1) Split (strategy default = auto)
    chunks = _chunk(body_text, strategy)
    if not chunks:
        return out

    # 2) Write chunks to MySQL (pending)
    chunk_ids = db.insert_chunks(chunks, material_id, knowledge_entry_id, class_id)
    out["chunk_ids"] = chunk_ids

    # 3) Embed batch
    texts = [c.text for c in chunks]
    try:
        client = EmbedClient()
        vectors = client.embed_batch(texts)  # list[Optional[List[float]]]
    except Exception as e:
        # Embed client raised (not just partial failure) — mark ALL failed
        db.mark_chunks_failed(chunk_ids)
        out["n_failed"] = len(chunk_ids)
        out["embed_error"] = str(e)
        return out

    # 4) Qdrant upsert (only the successfully-embedded chunks)
    ready_ids = []
    failed_ids = []
    for cid, vec in zip(chunk_ids, vectors):
        if vec is None:
            failed_ids.append(cid)
        else:
            ready_ids.append(cid)

    try:
        store = QdrantStore()
        store.ensure_collection()
        if ready_ids:
            payloads = [
                {
                    "class_id": class_id,
                    "material_id": material_id,
                    "knowledge_entry_id": knowledge_entry_id,
                    "chunk_id": rid,   # = knowledge_chunks.id (spec line 31)
                    "chunk_index": chunks[chunk_ids.index(rid)].index,
                }
                for rid in ready_ids
            ]
            ready_vecs = [vectors[chunk_ids.index(rid)] for rid in ready_ids]
            store.upsert(ready_ids, ready_vecs, payloads)
        out["qdrant_written"] = True
    except Exception as e:
        # Qdrant failure → don't write ANY vectors; mark everything failed
        store_error = str(e)
        db.mark_chunks_failed(chunk_ids)
        out["n_failed"] = len(chunk_ids)
        out["qdrant_error"] = store_error
        return out

    # 5) Mark MySQL rows
    if ready_ids:
        db.mark_chunks_ready(ready_ids, embedding_dim=Config.EMBEDDING_DIM)
    if failed_ids:
        db.mark_chunks_failed(failed_ids)

    out["n_ready"] = len(ready_ids)
    out["n_failed"] = len(failed_ids)
    return out


def reindex(material_id: int, class_id: int, strategy: str | None = None) -> dict:
    """Re-index an existing material: DELETE old chunks + old vectors,
    then re-run post_upload with the chosen strategy."""
    # Verify material still belongs to caller's class (reindex must be teacher of that class)
    mat = db.get_material(material_id, class_id)
    if mat is None:
        return {"error": "not found", "material_id": material_id}, 404
    ke = db.get_knowledge_entry(material_id, class_id)
    if ke is None:
        return {"error": "no knowledge entry", "material_id": material_id}, 400

    # 1) Delete old MySQL chunks
    db.delete_chunks_by_material_id(material_id)

    # 2) Delete old Qdrant vectors
    try:
        QdrantStore().delete_by_material_id(material_id)
    except Exception:
        pass  # Qdrant may be unavailable; MySQL side is the source of truth

    # 3) Re-run
    return post_upload(material_id, ke["id"], class_id, ke["body_text"], strategy)


def ensure_seed_indexes():
    """Run at init_db end: for every material that has a knowledge_entry but
    NO knowledge_chunks yet, index it with the default 'auto' strategy."""
    with db.get_conn() as conn:
        materials = db._rows(conn,
            "SELECT m.id, m.class_id, m.uploader_id, ke.id AS ke_id, ke.body_text "
            "FROM materials m "
            "JOIN knowledge_entries ke ON ke.material_id = m.id "
            "LEFT JOIN knowledge_chunks kc ON kc.material_id = m.id "
            "WHERE kc.id IS NULL",
        )

    for row in materials:
        try:
            post_upload(row["id"], row["ke_id"], row["class_id"],
                        row["body_text"], strategy="auto")
        except Exception:
            traceback.print_exc()
