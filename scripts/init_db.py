"""MySQL schema + seed script + Qdrant collection creation.

Idempotent: safe to run multiple times.
Requires MySQL reachable and pymysql / qdrant-client installed.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.config import Config
from app.db import get_conn, _one, _exec
from app.security import hash_password


SCHEMA = """
CREATE TABLE IF NOT EXISTS classes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(128) NOT NULL UNIQUE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(128) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    role ENUM('teacher','student') NOT NULL,
    class_id INT NOT NULL REFERENCES classes(id),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_users_class_id (class_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS materials (
    id INT AUTO_INCREMENT PRIMARY KEY,
    title VARCHAR(512) NOT NULL,
    class_id INT NOT NULL REFERENCES classes(id),
    uploader_id INT NOT NULL REFERENCES users(id),
    file_path VARCHAR(1024) NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_materials_class_id (class_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS knowledge_entries (
    id INT AUTO_INCREMENT PRIMARY KEY,
    material_id INT NOT NULL REFERENCES materials(id),
    class_id INT NOT NULL REFERENCES classes(id),
    body_text MEDIUMTEXT NOT NULL,
    INDEX idx_ke_material (material_id),
    INDEX idx_ke_class (class_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Retrieval: sliced chunks from knowledge_entries.body_text
CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id INT AUTO_INCREMENT PRIMARY KEY,
    knowledge_entry_id INT NOT NULL REFERENCES knowledge_entries(id),
    material_id INT NOT NULL REFERENCES materials(id),
    class_id INT NOT NULL,
    chunk_index INT NOT NULL,
    char_start INT NOT NULL,
    char_end INT NOT NULL,
    chunk_text MEDIUMTEXT NOT NULL,
    index_status ENUM('pending','ready','failed') NOT NULL DEFAULT 'pending',
    embedding_dim INT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_kc_class_ready (class_id, index_status),
    INDEX idx_kc_material (material_id),
    UNIQUE KEY uq_kc_material_index (material_id, chunk_index)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Domain placeholder tables (讲义、作业、助手、技能) — from prior lessons
CREATE TABLE IF NOT EXISTS lecture_notes (id INT AUTO_INCREMENT PRIMARY KEY, class_id INT NOT NULL, title VARCHAR(255)) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS assignments (id INT AUTO_INCREMENT PRIMARY KEY, class_id INT NOT NULL, title VARCHAR(255), due_date VARCHAR(64)) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS assistants (id INT AUTO_INCREMENT PRIMARY KEY, class_id INT NOT NULL, title VARCHAR(255)) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS skills (id INT AUTO_INCREMENT PRIMARY KEY, class_id INT NOT NULL, title VARCHAR(255)) ENGINE=InnoDB;
"""


def _ensure_fulltext_index():
    """CREATE FULLTEXT INDEX with ngram parser — only if it doesn't exist yet."""
    with get_conn() as conn:
        row = _one(conn,
            "SELECT INDEX_NAME FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'knowledge_chunks' AND INDEX_NAME = 'ft_kc_text'"
        )
        if not row:
            _exec(conn,
                "CREATE FULLTEXT INDEX ft_kc_text ON knowledge_chunks(chunk_text) "
                "WITH PARSER ngram"
            )
            print("[init_db] Created FULLTEXT INDEX ft_kc_text (ngram)")
        else:
            print("[init_db] FULLTEXT INDEX ft_kc_text already exists")


def ensure_tables():
    print("[init_db] Creating tables (MySQL) ...")
    with get_conn() as conn:
        for stmt in SCHEMA.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                _exec(conn, stmt)
    _ensure_fulltext_index()


def _ensure_qdrant_collection():
    """Create Qdrant collection if missing; skip if qdrant-client not importable."""
    try:
        from qdrant_client import QdrantClient, models
    except ImportError:
        print("[init_db] qdrant-client not installed — skipping collection create")
        return
    try:
        client = QdrantClient(url=Config.QDRANT_URL)
        coll_name = Config.QDRANT_COLLECTION
        existing = [c.name for c in client.get_collections().collections]
        if coll_name in existing:
            print(f"[init_db] Qdrant collection '{coll_name}' exists")
            return
        dim = Config.EMBEDDING_DIM
        if dim is None:
            print("[init_db] EMBEDDING_DIM not set — cannot create Qdrant collection")
            return
        client.create_collection(
            collection_name=coll_name,
            vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE),
        )
        print(f"[init_db] Created Qdrant collection '{coll_name}' (dim={dim}, COSINE)")
    except Exception as e:
        print(f"[init_db] Qdrant collection create skipped: {e}")


def seed_data():
    upload_dir = Config.UPLOAD_DIR
    os.makedirs(upload_dir, exist_ok=True)

    with get_conn() as conn:
        # --- Classes ---
        for name in ("班级 A", "班级 B"):
            _exec(conn, "INSERT IGNORE INTO classes (name) VALUES (%s)", (name,))
        class_a = _scalar(conn, "SELECT id FROM classes WHERE name = '班级 A'")
        class_b = _scalar(conn, "SELECT id FROM classes WHERE name = '班级 B'")

        # --- Users ---
        rows = [
            ("teacher_a",  "teacher", class_a, "teacher123"),
            ("student_a1", "student", class_a, "student123"),
            ("student_b1", "student", class_b, "student123"),
        ]
        for username, role, cid, pw in rows:
            existing = _scalar(conn, "SELECT id FROM users WHERE username = %s", (username,))
            if not existing:
                _exec(conn,
                    "INSERT INTO users (username, password_hash, role, class_id) VALUES (%s, %s, %s, %s)",
                    (username, hash_password(pw), role, cid),
                )
        teacher_a_id = _scalar(conn, "SELECT id FROM users WHERE username = 'teacher_a'")

        # --- Domain placeholder tables ---
        for t, title in [("lecture_notes","教学讲义占位行"),("assignments","作业占位行"),("assistants","助手占位行"),("skills","技能占位行")]:
            if _scalar(conn, f"SELECT COUNT(*) FROM {t}") == 0:
                _exec(conn, f"INSERT INTO {t} (class_id, title) VALUES (%s, %s)", (class_a, title))

        # --- Sample files + materials + knowledge_entries ---
        samples = [
            ("sample_a.md", class_a, "A 班示例讲义：认识分数",
             "# 班级 A 示例讲义\n\n本文档属于班级 A 的样本材料。\n\n## 分数基础\n\n分子是分数的一部分，表示被取出的份数。分母表示把整体分成了多少份。\n\n### 加减法\n\n同分母分数相加减，分母不变，只把分子相加减。\n\n例：1/5 + 2/5 = 3/5。",
             f"{upload_dir}/sample_a.md"),
            ("sample_b.md", class_b, "B 班示例讲义：分数的加法",
             "# 班级 B 示例讲义\n\n本文档属于班级 B 的样本材料。\n\n## 分数加法\n\n### 同分母加法\n\n同分母分数相加，分子相加、分母不变。\n\n### 异分母加法\n\n先通分：找到两个分母的最小公倍数，把每个分数化成分母相同的形式，再按同分母相加。\n\n例：1/2 + 1/3 = 3/6 + 2/6 = 5/6。",
             f"{upload_dir}/sample_b.md"),
        ]

        for fname, cid, title, content, disk_path in samples:
            with open(disk_path, "w", encoding="utf-8") as f:
                f.write(content)
            existing = _scalar(conn, "SELECT id FROM materials WHERE title = %s AND class_id = %s", (title, cid))
            if not existing:
                _exec(conn,
                    "INSERT INTO materials (title, class_id, uploader_id, file_path) VALUES (%s, %s, %s, %s)",
                    (title, cid, teacher_a_id, disk_path),
                )
                mid = _scalar(conn, "SELECT LAST_INSERT_ID()")
                _exec(conn,
                    "INSERT INTO knowledge_entries (material_id, class_id, body_text) VALUES (%s, %s, %s)",
                    (mid, cid, content),
                )

    print("[init_db] Seed data inserted: 2 classes, 3 users, 2 materials + knowledge, 4 domain rows.")


def _scalar(conn, sql, params=()):
    row = _one(conn, sql, params)
    if row is None:
        return None
    # pymysql DictCursor row["id"] if column is named, else row[0]
    for key in ("COUNT(*)", "COUNT(*) AS c", "id", "LAST_INSERT_ID()"):
        if key in row:
            return row[key]
    return next(iter(row.values()))


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="DROP tables first (WARNING)")
    parser.add_argument("--skip-seed-index", action="store_true",
                        help="Skip auto-embedding of seed materials (gateway may be unreachable)")
    args = parser.parse_args()

    if args.force:
        print("[init_db] --force: DROPPING ALL TABLES")
        with get_conn() as conn:
            _exec(conn, "SET FOREIGN_KEY_CHECKS = 0")
            for t in ("knowledge_chunks","knowledge_entries","materials","users","classes",
                      "lecture_notes","assignments","assistants","skills"):
                _exec(conn, f"DROP TABLE IF EXISTS {t}")
            _exec(conn, "SET FOREIGN_KEY_CHECKS = 1")

    ensure_tables()
    seed_data()
    _ensure_qdrant_collection()

    if not args.skip_seed_index:
        try:
            from app.indexing import ensure_seed_indexes
            print("[init_db] Ensuring seed material indexes (auto strategy) ...")
            ensure_seed_indexes()
        except Exception as e:
            print(f"[init_db] Seed indexing skipped (gateway may be unreachable): {e}")

    print("[init_db] Done.")


if __name__ == "__main__":
    main()
