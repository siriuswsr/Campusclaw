"""MySQL connection and query helpers — ALL material queries force class_id filtering.

DATABASE: MySQL 8.0 (FULLTEXT ngram parser on knowledge_chunks).
Connection via pymysql with DictCursor so rows behave like dict-like objects.

Pymysql note: Connection has NO .execute() — must use .cursor().execute().
"""
import pymysql
from pymysql.cursors import DictCursor
from contextlib import contextmanager
from .config import Config


@contextmanager
def get_conn():
    """Yield a MySQL connection with DictCursor. Auto-commit on success, rollback on exception."""
    conn = pymysql.connect(**Config.mysql_dsn(), cursorclass=DictCursor, autocommit=False)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _rows(conn, sql, params=()):
    cur = conn.cursor()
    cur.execute(sql, params)
    return cur.fetchall()


def _one(conn, sql, params=()):
    cur = conn.cursor()
    cur.execute(sql, params)
    return cur.fetchone()


def _exec(conn, sql, params=()):
    cur = conn.cursor()
    cur.execute(sql, params)
    return cur


# ---------------------------------------------------------------------------
# Material helpers — ALL force class_id filtering
# ---------------------------------------------------------------------------

def list_materials(class_id: int):
    with get_conn() as conn:
        rows = _rows(conn,
            "SELECT m.*, c.name AS class_name, u.username AS uploader_name "
            "FROM materials m "
            "JOIN classes c ON m.class_id = c.id "
            "JOIN users u ON m.uploader_id = u.id "
            "WHERE m.class_id = %s ORDER BY m.created_at DESC",
            (class_id,),
        )
        return [dict(r) for r in rows]


def get_material(material_id: int, class_id: int):
    with get_conn() as conn:
        row = _one(conn,
            "SELECT m.*, c.name AS class_name, u.username AS uploader_name "
            "FROM materials m "
            "JOIN classes c ON m.class_id = c.id "
            "JOIN users u ON m.uploader_id = u.id "
            "WHERE m.id = %s AND m.class_id = %s",
            (material_id, class_id),
        )
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# User lookup
# ---------------------------------------------------------------------------

def get_user_by_username(username: str):
    with get_conn() as conn:
        row = _one(conn, "SELECT * FROM users WHERE username = %s", (username,))
        return dict(row) if row else None


def get_user_by_id(user_id: int):
    with get_conn() as conn:
        row = _one(conn, "SELECT * FROM users WHERE id = %s", (user_id,))
        return dict(row) if row else None


def get_class_id_by_user_id(user_id: int) -> int | None:
    with get_conn() as conn:
        row = _one(conn, "SELECT class_id FROM users WHERE id = %s", (user_id,))
        return row["class_id"] if row else None


# ---------------------------------------------------------------------------
# Knowledge entries
# ---------------------------------------------------------------------------

def create_knowledge_entry(material_id: int, class_id: int, body_text: str) -> int:
    with get_conn() as conn:
        cur = _exec(conn,
            "INSERT INTO knowledge_entries (material_id, class_id, body_text) "
            "VALUES (%s, %s, %s)",
            (material_id, class_id, body_text),
        )
        return cur.lastrowid


def get_knowledge_entry(material_id: int, class_id: int):
    with get_conn() as conn:
        row = _one(conn,
            "SELECT * FROM knowledge_entries WHERE material_id = %s AND class_id = %s",
            (material_id, class_id),
        )
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# knowledge_chunks (retrieval)
# ---------------------------------------------------------------------------

def insert_chunks(chunks, material_id: int, knowledge_entry_id: int, class_id: int):
    """Bulk-insert chunks (index_status='pending'). chunks = list of Chunk dataclass."""
    if not chunks:
        return []
    ids = []
    with get_conn() as conn:
        for c in chunks:
            cur = _exec(conn,
                "INSERT INTO knowledge_chunks "
                "(knowledge_entry_id, material_id, class_id, chunk_index, char_start, char_end, chunk_text, index_status) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending')",
                (knowledge_entry_id, material_id, class_id, c.index, c.char_start, c.char_end, c.text),
            )
            ids.append(cur.lastrowid)
    return ids


def mark_chunks_ready(chunk_ids, embedding_dim: int | None = None):
    if not chunk_ids:
        return
    with get_conn() as conn:
        placeholders = ",".join(["%s"] * len(chunk_ids))
        if embedding_dim is not None:
            _exec(conn,
                f"UPDATE knowledge_chunks SET index_status='ready', embedding_dim=%s WHERE id IN ({placeholders})",
                [embedding_dim, *chunk_ids],
            )
        else:
            _exec(conn,
                f"UPDATE knowledge_chunks SET index_status='ready' WHERE id IN ({placeholders})",
                list(chunk_ids),
            )


def mark_chunks_failed(chunk_ids):
    if not chunk_ids:
        return
    with get_conn() as conn:
        placeholders = ",".join(["%s"] * len(chunk_ids))
        _exec(conn,
            f"UPDATE knowledge_chunks SET index_status='failed' WHERE id IN ({placeholders})",
            list(chunk_ids),
        )


def delete_chunks_by_material_id(material_id: int):
    with get_conn() as conn:
        _exec(conn, "DELETE FROM knowledge_chunks WHERE material_id = %s", (material_id,))


def get_chunks_by_material_id(material_id: int):
    with get_conn() as conn:
        rows = _rows(conn,
            "SELECT * FROM knowledge_chunks WHERE material_id = %s ORDER BY chunk_index",
            (material_id,),
        )
        return [dict(r) for r in rows]


def get_chunks_by_ids_and_class(chunk_ids: list[int], class_id: int):
    """回表时必须再次核对 class_id — 即使 Qdrant 漏加过滤也能排除他班记录 (spec line 87)."""
    if not chunk_ids:
        return []
    with get_conn() as conn:
        placeholders = ",".join(["%s"] * len(chunk_ids))
        rows = _rows(conn,
            f"SELECT kc.*, m.title AS material_title "
            f"FROM knowledge_chunks kc "
            f"JOIN materials m ON m.id = kc.material_id "
            f"WHERE kc.id IN ({placeholders}) AND kc.class_id = %s",
            [*chunk_ids, class_id],
        )
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Keyword search — MySQL FULLTEXT with ngram parser
# ---------------------------------------------------------------------------

def keyword_search(query: str, class_id: int, top_k: int = 20):
    """MySQL FULLTEXT only; does NOT call embed service or Qdrant (spec line 51-53)."""
    with get_conn() as conn:
        # NATURAL LANGUAGE MODE works with ngram parser for Chinese tokenization.
        # BOOLEAN MODE does NOT support WITH QUERY EXPANSION — that's only for NATURAL LANGUAGE.
        rows = _rows(conn,
            "SELECT kc.*, m.title AS material_title, "
            "MATCH(kc.chunk_text) AGAINST(%s IN NATURAL LANGUAGE MODE) AS relevance "
            "FROM knowledge_chunks kc "
            "JOIN materials m ON m.id = kc.material_id "
            "WHERE kc.class_id = %s AND kc.index_status = 'ready' "
            "AND MATCH(kc.chunk_text) AGAINST(%s IN NATURAL LANGUAGE MODE) "
            "ORDER BY relevance DESC "
            "LIMIT %s",
            (query, class_id, query, top_k),
        )
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Qdrant reachability (used by retrieval.py to decide 503 vs normal)
# ---------------------------------------------------------------------------

def mysql_ping():
    """Return True iff MySQL is reachable and responding."""
    try:
        with get_conn() as conn:
            _one(conn, "SELECT 1")
        return True
    except Exception:
        return False
