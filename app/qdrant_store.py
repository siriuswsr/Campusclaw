"""Qdrant client wrapper — campusclaw_chunks collection, COSINE distance.

Payload design (spec line 31):
  class_id, material_id, knowledge_entry_id, chunk_id (= pk), chunk_index
  -- MUST NOT contain chunk_text.
Vector primary key = knowledge_chunks.id (an integer) = payload.chunk_id (spec line 31).
"""
from typing import List, Optional
from .config import Config


class QdrantStore:
    """Thin wrapper around qdrant_client.QdrantClient. Gracefully handles
    connection failures so retrieval.py can surface 503 vs normal results."""

    def __init__(self):
        from qdrant_client import QdrantClient, models
        self.client = QdrantClient(url=Config.QDRANT_URL)
        self.models = models
        self.collection = Config.QDRANT_COLLECTION
        self.vector_dim = Config.EMBEDDING_DIM

    # --- availability ---

    def is_available(self) -> bool:
        try:
            self.client.get_collection(self.collection)
            return True
        except Exception:
            return False

    # --- lifecycle ---

    def ensure_collection(self):
        """Create collection if missing. Idempotent — skip if exists."""
        from qdrant_client import models
        try:
            existing = [c.name for c in self.client.get_collections().collections]
            if self.collection in existing:
                return
            self.client.create_collection(
                collection_name=self.collection,
                vectors_config=models.VectorParams(
                    size=self.vector_dim,
                    distance=models.Distance.COSINE,
                ),
            )
        except Exception:
            pass  # startup race or client not reachable — caller handles

    # --- upsert / delete ---

    def upsert(self, chunk_ids: List[int], vectors: List[List[float]],
               payloads: List[dict]) -> None:
        """Insert / update vectors. All three lists MUST have matching lengths.
        Raises Exception on transport failure — caller decides if this is fatal."""
        points = [
            self.models.PointStruct(id=cid, vector=vec, payload=p)
            for cid, vec, p in zip(chunk_ids, vectors, payloads)
        ]
        self.client.upsert(collection_name=self.collection, points=points, wait=True)

    def delete_by_material_id(self, material_id: int) -> None:
        """Remove all vectors whose payload.material_id = material_id."""
        try:
            self.client.delete(
                collection_name=self.collection,
                points_selector=self.models.FilterSelector(
                    filter=self.models.Filter(must=[
                        self.models.FieldCondition(
                            key="material_id",
                            match=self.models.MatchValue(value=material_id),
                        )
                    ])
                ),
                wait=True,
            )
        except Exception:
            pass  # Qdrant may be unavailable — caller decides

    def delete_by_chunk_ids(self, chunk_ids: List[int]) -> None:
        if not chunk_ids:
            return
        try:
            self.client.delete(
                collection_name=self.collection,
                points=self.models.PointsIdsList(ids=[int(x) for x in chunk_ids]),
                wait=True,
            )
        except Exception:
            pass

    # --- search ---

    def search(self, query_vec: List[float], class_id: int,
               top_k: int = 20, score_threshold: float = 0.35,
               material_ids: Optional[List[int]] = None) -> List[dict]:
        """Cosine search filtered by class_id payload. Skips candidates below threshold.

        提示词 E：material_ids 非空时再加一层 payload.material_id ∈ [...] 过滤，
        让「智能体绑定的材料」在向量路径上就收窄，而不是等回表才丢。

        Each result: {"chunk_id": int, "score": float, "payload": dict}
        Only returns Qdrant ids — caller joins back to MySQL to get chunk_text."""
        must = [
            self.models.FieldCondition(
                key="class_id",
                match=self.models.MatchValue(value=class_id),
            )
        ]
        if material_ids:
            must.append(self.models.FieldCondition(
                key="material_id",
                match=self.models.MatchAny(any=[int(x) for x in material_ids]),
            ))
        results = self.client.search(
            collection_name=self.collection,
            query_vector=query_vec,
            limit=top_k,
            score_threshold=score_threshold,
            query_filter=self.models.Filter(must=must),
            with_payload=True,
        )
        return [{"chunk_id": r.id, "score": r.score, "payload": dict(r.payload or {})}
                for r in results]
