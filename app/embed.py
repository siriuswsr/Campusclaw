"""OpenAI-compatible embedding gateway client.

MUST be the ONLY place that calls the embedding service; chunker.py and
materials.py upload flow do NOT embed anything themselves. (spec line 4:
嵌入模块 MUST NOT be triggered by chunk module.)
"""
from typing import List, Optional
import requests
from .config import Config


class EmbedError(Exception):
    pass


class EmbedClient:
    """OpenAI /embeddings compatible client. URL / KEY / MODEL all from env."""

    def __init__(self, url: str | None = None, key: str | None = None, model: str | None = None,
                 dim: int | None = None):
        self.url = (url or Config.EMBEDDING_URL).rstrip("/") + "/embeddings"
        self.key = key or Config.EMBEDDING_KEY
        self.model = model or Config.EMBEDDING_MODEL
        self.expected_dim = dim if dim is not None else Config.EMBEDDING_DIM

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.key}",
        }

    def embed_single(self, text: str) -> List[float]:
        """Embed a single string; raise EmbedError on failure or dimension mismatch."""
        result = self.embed_batch([text])
        v = result[0]
        if v is None:
            raise EmbedError("embedding failed for single text")
        return v

    def embed_batch(self, texts: List[str], batch_size: int = 32) -> List[Optional[List[float]]]:
        """Embed N texts; returns a list the same length as input with None for failures.

        Retries once per batch on transient errors; after that returns None for each
        item in the batch (mark the chunk as 'failed' in MySQL, no partial Qdrant write)."""
        results: List[Optional[List[float]]] = [None] * len(texts)
        if not texts:
            return results

        for start in range(0, len(texts), batch_size):
            batch = texts[start:start + batch_size]
            batch_result = self._call_once(batch)
            if batch_result is None:
                # Retry once
                batch_result = self._call_once(batch)
            if batch_result is None:
                # Whole batch failed → mark all None (caller will mark MySQL chunks failed)
                for offset in range(len(batch)):
                    results[start + offset] = None
            else:
                for i, vec in enumerate(batch_result):
                    if vec is not None:
                        if len(vec) != self.expected_dim:
                            results[start + i] = None
                        else:
                            results[start + i] = vec
                    else:
                        results[start + i] = None
        return results

    def _call_once(self, texts: List[str]) -> Optional[List[Optional[List[float]]]]:
        """Single HTTP call. Returns None on any transport / HTTP / response error.
        Returns a list of vecs (some may be None if gateway returned partial failures)."""
        try:
            resp = requests.post(
                self.url,
                headers=self._headers(),
                json={"model": self.model, "input": texts},
                timeout=30,
            )
            if resp.status_code != 200:
                return None
            payload = resp.json()
            data = payload.get("data", [])
            by_idx: dict[int, Optional[List[float]]] = {}
            for entry in data:
                i = entry.get("index")
                emb = entry.get("embedding")
                if i is not None and isinstance(emb, list):
                    by_idx[i] = emb
            out: List[Optional[List[float]]] = [by_idx.get(i) for i in range(len(texts))]
            return out
        except Exception:
            return None


def embed_single(text: str) -> Optional[List[float]]:
    """Module-level shortcut (recommended for one-offs where you don't need batch semantics)."""
    try:
        return EmbedClient().embed_single(text)
    except EmbedError:
        return None
