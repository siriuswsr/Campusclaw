"""Retrieval module — keyword / vector / hybrid (RRF).

Class isolation: class_id ALWAYS from session; caller-supplied query/header/JSON
class_id is IGNORED. MySQL and Qdrant are both filtered BY session class_id.

Spec notes:
  - Qdrant unavailable → keyword still works; vector/hybrid MUST return 503 (no fake scores).
  - Qdrant unavailable → hybrid degraded to keyword ONLY when keyword is explicitly
    requested? NO — spec says vector AND hybrid both 503. No "partial hybrid".
  - MySQL回表 MUST re-filter class_id (spec line 87).
"""
from dataclasses import dataclass, field
from typing import List, Optional
from . import db
from .config import Config


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------

@dataclass
class Hit:
    chunk_id: int
    material_id: int
    material_title: str
    chunk_index: int
    char_start: int
    char_end: int
    excerpt: str             # from MySQL chunk_text (≤300 chars typically)
    mode: str                # "keyword" | "vector" | "hybrid"
    score: float             # MySQL relevance, Qdrant cosine, or RRF score


@dataclass
class SearchResult:
    hits: List[Hit]
    mode: str
    total: int
    degraded: bool = False
    reason: Optional[str] = None

    def to_json(self) -> dict:
        return {
            "hits": [
                {
                    "chunk_id": h.chunk_id,
                    "material_id": h.material_id,
                    "material_title": h.material_title,
                    "chunk_index": h.chunk_index,
                    "char_start": h.char_start,
                    "char_end": h.char_end,
                    "excerpt": h.excerpt[:300],
                    "mode": h.mode,
                    "score": h.score,
                }
                for h in self.hits
            ],
            "mode": self.mode,
            "total": len(self.hits),
            "degraded": self.degraded,
            "reason": self.reason,
        }


# ---------------------------------------------------------------------------
# Public search entry — called only by search route (which enforces @login_required)
# ---------------------------------------------------------------------------

def search(mode: str, query: str, class_id: int, top_k: int = 20) -> SearchResult:
    """Dispatch; handles Qdrant / MySQL failures per spec.

    class_id MUST come from session — this function does NOT read from request.
    """
    if mode not in ("keyword", "vector", "hybrid"):
        raise ValueError(f"invalid mode: {mode}")

    if mode == "keyword":
        return _keyword_only(query, class_id, top_k)
    if mode == "vector":
        return _vector_only(query, class_id, top_k)
    # hybrid
    return _hybrid(query, class_id, top_k)


# ---------------------------------------------------------------------------
# Path implementations
# ---------------------------------------------------------------------------

def _keyword_only(query: str, class_id: int, top_k: int) -> SearchResult:
    """MySQL FULLTEXT ngram ONLY. No embed, no Qdrant.

    Empty/too-short queries → return empty hits cleanly (caller already
    validated non-empty at the route level, but MySQL may return no results)."""
    try:
        rows = db.keyword_search(query, class_id, top_k)
    except Exception:
        # MySQL failure → not our problem (caller wraps in 503 at route level)
        raise
    hits = [
        Hit(
            chunk_id=int(r["id"]),
            material_id=int(r["material_id"]),
            material_title=r.get("material_title", ""),
            chunk_index=int(r["chunk_index"]),
            char_start=int(r["char_start"]),
            char_end=int(r["char_end"]),
            excerpt=r["chunk_text"],
            mode="keyword",
            score=float(r["relevance"] or 0.0),
        )
        for r in rows
    ]
    return SearchResult(hits=hits, mode="keyword", total=len(hits))


def _vector_only(query: str, class_id: int, top_k: int) -> SearchResult:
    """Embed query → Qdrant class_id filter → 0.35 threshold → 回表核班级."""
    from .embed import EmbedClient
    from .qdrant_store import QdrantStore

    try:
        qvec = EmbedClient().embed_single(query)
    except Exception:
        raise QdrantUnavailable("embed gateway unreachable or failed")

    try:
        store = QdrantStore()
        q_results = store.search(qvec, class_id, top_k=top_k,
                                 score_threshold=Config.VECTOR_SCORE_THRESHOLD)
    except Exception as e:
        raise QdrantUnavailable(f"qdrant unreachable: {e}")

    if not q_results:
        return SearchResult(hits=[], mode="vector", total=0)

    # 回表 — re-filter class_id (spec line 87 safety belt)
    chunk_ids = [r["chunk_id"] for r in q_results]
    try:
        rows = db.get_chunks_by_ids_and_class(chunk_ids, class_id)
    except Exception:
        raise
    rows_by_id = {int(r["id"]): r for r in rows}

    hits = []
    for r in q_results:
        cid = r["chunk_id"]
        row = rows_by_id.get(cid)
        if row is None:
            continue  # cross-class exclusion via re-filter
        hits.append(Hit(
            chunk_id=cid,
            material_id=int(row["material_id"]),
            material_title=row.get("material_title", ""),
            chunk_index=int(row["chunk_index"]),
            char_start=int(row["char_start"]),
            char_end=int(row["char_end"]),
            excerpt=row["chunk_text"],
            mode="vector",
            score=float(r["score"]),
        ))
    return SearchResult(hits=hits, mode="vector", total=len(hits))


class QdrantUnavailable(Exception):
    pass


def _hybrid(query: str, class_id: int, top_k: int) -> SearchResult:
    """Run both paths; RRF merge (k=60). IF EITHER path fails (embed/Qdrant
    UNREACHABLE) this function raises QdrantUnavailable — spec line 142 says
    hybrid returns 503 when Qdrant is down. Hybrid is NOT degraded to keyword."""
    keyword_hits = _keyword_path_for_hybrid(query, class_id, top_k)
    try:
        vector_result = _vector_only(query, class_id, top_k)
        vector_hits = vector_result.hits
    except QdrantUnavailable:
        # spec line 142 — hybrid MUST return 503 when Qdrant is down
        raise
    except Exception as e:
        raise QdrantUnavailable(f"vector path failed in hybrid: {e}")

    merged = _rrf_merge(keyword_hits, vector_hits, k=Config.RRF_K, top_k=top_k)
    return SearchResult(hits=merged, mode="hybrid", total=len(merged))


def _keyword_path_for_hybrid(query: str, class_id: int, top_k: int) -> List[Hit]:
    """Same as keyword_only but returns raw hits list for RRF consumption."""
    result = _keyword_only(query, class_id, top_k)
    return result.hits


# ---------------------------------------------------------------------------
# RRF merge — k=60 default
# ---------------------------------------------------------------------------

def _rrf_merge(keyword_hits: List[Hit], vector_hits: List[Hit],
               k: int = 60, top_k: int = 20) -> List[Hit]:
    """Rank Reciprocal Rank Fusion. Scores are NOT directly added — they are
    ranks converted to 1/(k+rank). Absent paths simply contribute nothing.

    spec line 69-71: k=60, ranks only, absent paths contribute nothing."""
    scores: dict[int, float] = {}
    best_hit: dict[int, Hit] = {}

    for rank, hit in enumerate(keyword_hits, start=1):
        scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (k + rank)
        # Prefer whichever hit we encounter first; keep its data for output
        best_hit.setdefault(hit.chunk_id, hit)

    for rank, hit in enumerate(vector_hits, start=1):
        scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (k + rank)
        best_hit.setdefault(hit.chunk_id, hit)

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    out = []
    for cid, s in ranked[:top_k]:
        h = best_hit[cid]
        # Replace score with RRF rank-fusion score; mode → hybrid
        out.append(Hit(
            chunk_id=h.chunk_id,
            material_id=h.material_id,
            material_title=h.material_title,
            chunk_index=h.chunk_index,
            char_start=h.char_start,
            char_end=h.char_end,
            excerpt=h.excerpt,
            mode="hybrid",
            score=s,
        ))
    return out
