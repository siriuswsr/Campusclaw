"""MySQL schema + seed script + Qdrant collection creation.

Idempotent: safe to run multiple times.
Requires MySQL reachable and pymysql / qdrant-client installed.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.config import Config
from app.db import get_conn, _one, _rows, _exec
from app.models import (HOMEWORK_SCHEMA, HOMEWORK_TABLES, TUTOR_SCHEMA, TUTOR_TABLES,
                        MCP_SCHEMA, MCP_TABLES)
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
    subject VARCHAR(32) NULL,
    class_id INT NOT NULL REFERENCES classes(id),
    uploader_id INT NOT NULL REFERENCES users(id),
    file_path VARCHAR(1024) NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_materials_class_id (class_id),
    INDEX idx_materials_subject (subject)
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

-- 讲义、助手 占位表（来自较早课程）。skills 占位表不在此列：
-- 已由 _drop_legacy_skills() 清理，正式结构见 app/models.py 的 _SKILLS_DDL。
CREATE TABLE IF NOT EXISTS lecture_notes (id INT AUTO_INCREMENT PRIMARY KEY, class_id INT NOT NULL, title VARCHAR(255)) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS assistants (id INT AUTO_INCREMENT PRIMARY KEY, class_id INT NOT NULL, title VARCHAR(255)) ENGINE=InnoDB;

-- Assistant chat config (lesson 5): prompt per subject + per-subject skill switch
CREATE TABLE IF NOT EXISTS assistant_prompts (
    id INT AUTO_INCREMENT PRIMARY KEY,
    subject VARCHAR(128) NOT NULL UNIQUE,
    prompt TEXT NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS assistant_skills (
    id INT AUTO_INCREMENT PRIMARY KEY,
    subject VARCHAR(128) NOT NULL,
    skill_name VARCHAR(128) NOT NULL,
    enabled TINYINT(1) NOT NULL DEFAULT 0,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_assistant_skill (subject, skill_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
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


def _drop_legacy_assignments():
    """删掉第 4 课的 assignments 占位表，给作业流程的正式结构让位。

    占位表是 (class_id, title, due_date)、只有一行 seed 垃圾数据，与作业流程的
    字段完全不兼容。表名相同会让 CREATE TABLE IF NOT EXISTS 整条跳过 —— 不删的话
    新表永远建不出来，而 `from app.models import *` 照样不报错，属于假通过。
    只在确认是旧结构（有 due_date、没有 body）时动手，幂等。
    """
    with get_conn() as conn:
        exists = _one(conn,
            "SELECT COUNT(*) AS c FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'assignments'")
        if not exists or not list(exists.values())[0]:
            return
        cols = {r["Field"] for r in _rows(conn, "SHOW COLUMNS FROM assignments")}
        if "due_date" in cols and "body" not in cols:
            print("[init_db] Dropping LEGACY placeholder table 'assignments' "
                  "(class_id, title, due_date) — replaced by the homework-flow schema")
            _exec(conn, "DROP TABLE assignments")


def _ensure_materials_subject():
    """向已存在的 materials 表补齐 subject 列（幂等）。

    CREATE TABLE IF NOT EXISTS 会跳过已存在的表，所以老库（比如测试环境里
    已经 init 过的库）不会因为改了 SCHEMA 就自动多出 subject 列。这里按
    information_schema 判断列是否存在，缺了才 ALTER —— 等价于「启动时幂等执行
    ALTER TABLE materials ADD COLUMN subject VARCHAR(32)」。
    """
    with get_conn() as conn:
        row = _one(conn,
            "SELECT COUNT(*) AS c FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'materials' "
            "AND COLUMN_NAME = 'subject'")
        if row and list(row.values())[0]:
            print("[init_db] materials.subject column already exists")
            return
        _exec(conn, "ALTER TABLE materials ADD COLUMN subject VARCHAR(32) NULL, "
                    "ADD INDEX idx_materials_subject (subject)")
        print("[init_db] Added materials.subject VARCHAR(32) NULL")


def _ensure_tutor_agents_subject():
    """向已存在的 tutor_agents 表补齐 subject 列（幂等）。

    CREATE TABLE IF NOT EXISTS 会跳过已存在的表，所以老库不会因为改了
    _TUTOR_AGENTS_DDL 就自动多出 subject 列。这里按 information_schema 判断列
    是否存在，缺了才 ALTER（等价于启动时幂等执行 ALTER TABLE tutor_agents
    ADD COLUMN subject VARCHAR(32) NULL）。
    """
    with get_conn() as conn:
        row = _one(conn,
            "SELECT COUNT(*) AS c FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'tutor_agents' "
            "AND COLUMN_NAME = 'subject'")
        if row and list(row.values())[0]:
            print("[init_db] tutor_agents.subject column already exists")
            return
        _exec(conn, "ALTER TABLE tutor_agents ADD COLUMN subject VARCHAR(32) NULL")
        print("[init_db] Added tutor_agents.subject VARCHAR(32) NULL")


def _ensure_solve_sessions_agent_id():
    """向已存在的 solve_sessions 表补齐 agent_id 列与索引（幂等，提示词 F 第 1 步）。

    CREATE TABLE IF NOT EXISTS 会跳过已存在的表，所以早期建过的 solve_sessions
    （若当时还没有 agent_id）不会自动多出这一列。按 information_schema 判断，
    缺列才 ALTER；外键只在新建表路径由 DDL 建立，老库补列时至少保证索引可用。
    """
    with get_conn() as conn:
        row = _one(conn,
            "SELECT COUNT(*) AS c FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'solve_sessions' "
            "AND COLUMN_NAME = 'agent_id'")
        if row and list(row.values())[0]:
            print("[init_db] solve_sessions.agent_id column already exists")
            return
        _exec(conn, "ALTER TABLE solve_sessions ADD COLUMN agent_id INT NULL")
        try:
            _exec(conn, "ALTER TABLE solve_sessions ADD INDEX idx_solve_sessions_agent (agent_id)")
        except Exception:
            pass
        print("[init_db] Added solve_sessions.agent_id INT NULL")


def _drop_legacy_skills():
    """删掉旧占位 skills(id, class_id, title)，给提示词 C 的正式结构让位。

    旧表是 (class_id, title) 三列、无 trigger_text/description，与正式表结构不兼容；
    表名相同会让 CREATE TABLE IF NOT EXISTS 整条跳过。只在确认是旧结构（有 title、
    没有 trigger_text）时动手，幂等。
    """
    with get_conn() as conn:
        exists = _one(conn,
            "SELECT COUNT(*) AS c FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'skills'")
        if not exists or not list(exists.values())[0]:
            return
        cols = {r["Field"] for r in _rows(conn, "SHOW COLUMNS FROM skills")}
        if "title" in cols and "trigger_text" not in cols:
            print("[init_db] Dropping LEGACY placeholder table 'skills' "
                  "(class_id, title) — replaced by the tutor-agent schema")
            _exec(conn, "DROP TABLE skills")


def ensure_tables():
    print("[init_db] Creating tables (MySQL) ...")
    _drop_legacy_assignments()
    _drop_legacy_skills()
    with get_conn() as conn:
        for stmt in SCHEMA.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                _exec(conn, stmt)
        # Homework flow: assignments / submissions / grades / wrong_questions / audit_log
        for stmt in HOMEWORK_SCHEMA:
            _exec(conn, stmt.strip())
        # Tutor agents / skills / 关联表 / solve_sessions（提示词 C）
        for stmt in TUTOR_SCHEMA:
            _exec(conn, stmt.strip())
        # MCP 服务器登记 + 工具发现缓存（提示词 B）
        for stmt in MCP_SCHEMA:
            _exec(conn, stmt.strip())
    _ensure_materials_subject()
    _ensure_tutor_agents_subject()
    _ensure_solve_sessions_agent_id()
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


# Assistant config defaults (lesson 5). Seeded one row at a time so a subject
# added later is picked up by an existing database, not just a fresh one.
SUBJECTS = ["math", "chinese", "english", "physics", "chemistry"]
DEFAULT_PROMPT_TEMPLATE = "你是{subject}学科的AI助教。请用通俗易懂的语言回答学生问题，必要时给出步骤解析。"
DEFAULT_SKILLS = ["solve_guide", "homework_grade", "wrong_answer"]


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
        # assignments 不在此列：作业流程的表要 teacher_id/body/subject/deadline，
        # 塞占位行会违反 NOT NULL（旧占位行由 _drop_legacy_assignments 一并清掉）。
        # skills 也不在此列：正式结构（提示词 C）字段多、约束严格，占位行不适用。
        for t, title in [("lecture_notes","教学讲义占位行"),("assistants","助手占位行")]:
            if _scalar(conn, f"SELECT COUNT(*) FROM {t}") == 0:
                _exec(conn, f"INSERT INTO {t} (class_id, title) VALUES (%s, %s)", (class_a, title))

        # --- Assistant config (lesson 5): 5 subjects × 3 skill switches ---
        for subject in SUBJECTS:
            if not _scalar(conn, "SELECT id FROM assistant_prompts WHERE subject = %s", (subject,)):
                _exec(conn,
                    "INSERT INTO assistant_prompts (subject, prompt) VALUES (%s, %s)",
                    (subject, DEFAULT_PROMPT_TEMPLATE.format(subject=subject)),
                )
            for skill_name in DEFAULT_SKILLS:
                if not _scalar(conn,
                               "SELECT id FROM assistant_skills WHERE subject = %s AND skill_name = %s",
                               (subject, skill_name)):
                    _exec(conn,
                        "INSERT INTO assistant_skills (subject, skill_name, enabled) VALUES (%s, %s, %s)",
                        (subject, skill_name, 1),
                    )

        # --- 预置两份种子技能（提示词 C）—— 按 seed_key 幂等去重 ---
        _seed_skill(conn, class_a, "math_solve_guide",
                    "数学解题引导",
                    "方程,去括号,分数,通分,面积,应用题,未知数,行程",
                    "数学",
                    "学生正在自己做数学作业，往往已经写了一步。先看懂他卡在哪一步，只指出下一步该检查什么，用提问引导他继续思考，不直接给出最终答案。")
        _seed_skill(conn, class_a, "chinese_solve_guide",
                    "语文解题引导",
                    "春晓,默写,阅读,中心,作文,开头,古诗,词语",
                    "语文",
                    "学生正在自己背诵、做阅读或改作文，卡在某一句。先接住他已经写出的那一句，指出下一步该看什么、怎么想，不直接把下一句或答案写出来。")

        # --- Sample files + materials + knowledge_entries ---
        samples = [
            ("sample_a.md", class_a, "A 班示例讲义：认识分数", "数学",
             "# 班级 A 示例讲义\n\n本文档属于班级 A 的样本材料。\n\n## 分数基础\n\n分子是分数的一部分，表示被取出的份数。分母表示把整体分成了多少份。\n\n### 加减法\n\n同分母分数相加减，分母不变，只把分子相加减。\n\n例：1/5 + 2/5 = 3/5。",
             f"{upload_dir}/sample_a.md"),
            ("sample_b.md", class_b, "B 班示例讲义：分数的加法", "数学",
             "# 班级 B 示例讲义\n\n本文档属于班级 B 的样本材料。\n\n## 分数加法\n\n### 同分母加法\n\n同分母分数相加，分子相加、分母不变。\n\n### 异分母加法\n\n先通分：找到两个分母的最小公倍数，把每个分数化成分母相同的形式，再按同分母相加。\n\n例：1/2 + 1/3 = 3/6 + 2/6 = 5/6。",
             f"{upload_dir}/sample_b.md"),
            ("math_notes.md", class_a, "数学自学笔记", "数学",
             "# 数学自学笔记\n\n## 一元一次方程\n\n方程是含有未知数的等式。解一元一次方程的基本步骤：去括号、移项、合并同类项、把未知数的系数化为 1。\n\n## 去括号\n\n去括号用的是乘法分配律：把括号外面的数分别乘到括号里的每一项。例如 2(x+3)=2x+6，先别丢项。\n\n## 分数与通分\n\n异分母分数相加减，要先通分，找到两个分母的最小公倍数作公分母，再按同分母相加减。",
             f"{upload_dir}/math_notes.md"),
            ("chinese_notes.md", class_a, "语文自学笔记", "语文",
             "# 语文自学笔记\n\n## 古诗默写\n\n默写古诗要先回想整首诗的意境和画面，按句推进。背不出的那一句，先想上一句的画面，再想作者接下来要说什么。\n\n## 《春晓》\n\n《春晓》写春天早晨：春眠不觉晓，处处闻啼鸟。夜来风雨声，花落知多少。",
             f"{upload_dir}/chinese_notes.md"),
        ]

        for fname, cid, title, subject, content, disk_path in samples:
            with open(disk_path, "w", encoding="utf-8") as f:
                f.write(content)
            existing = _scalar(conn, "SELECT id FROM materials WHERE title = %s AND class_id = %s", (title, cid))
            if not existing:
                _exec(conn,
                    "INSERT INTO materials (title, subject, class_id, uploader_id, file_path) VALUES (%s, %s, %s, %s, %s)",
                    (title, subject, cid, teacher_a_id, disk_path),
                )
                mid = _scalar(conn, "SELECT LAST_INSERT_ID()")
                _exec(conn,
                    "INSERT INTO knowledge_entries (material_id, class_id, body_text) VALUES (%s, %s, %s)",
                    (mid, cid, content),
                )

    print("[init_db] Seed data inserted: 2 classes, 3 users, 4 materials, 2 skills, 3 domain rows.")


def _scalar(conn, sql, params=()):
    row = _one(conn, sql, params)
    if row is None:
        return None
    # pymysql DictCursor row["id"] if column is named, else row[0]
    for key in ("COUNT(*)", "COUNT(*) AS c", "id", "LAST_INSERT_ID()"):
        if key in row:
            return row[key]
    return next(iter(row.values()))


def _seed_skill(conn, class_id, seed_key, name, trigger_text, subject_text, description):
    """幂等写入一份种子技能（按 seed_key 去重），source_text 还原成可回填全文。"""
    if _scalar(conn, "SELECT id FROM skills WHERE seed_key = %s", (seed_key,)):
        return
    source_text = (
        f"名称：{name}\n"
        f"适用条件：{trigger_text}\n"
        f"资料：{subject_text}\n"
        "工具：knowledge_search\n"
        "启用：是\n\n"
        f"{description}"
    )
    _exec(conn,
        "INSERT INTO skills (class_id, name, trigger_text, subject_text, tools_json, "
        "enabled, description, source_text, seed_key) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (class_id, name, trigger_text, subject_text, '["knowledge_search"]',
         1, description, source_text, seed_key))


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
            for t in HOMEWORK_TABLES + TUTOR_TABLES + [
                      "knowledge_chunks","knowledge_entries","materials","users","classes",
                      "lecture_notes","assistants",
                      "assistant_prompts","assistant_skills"]:
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
