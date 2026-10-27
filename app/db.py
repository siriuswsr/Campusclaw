"""MySQL connection and query helpers — ALL material queries force class_id filtering.

DATABASE: MySQL 8.0 (FULLTEXT ngram parser on knowledge_chunks).
Connection via pymysql with DictCursor so rows behave like dict-like objects.

Pymysql note: Connection has NO .execute() — must use .cursor().execute().
"""
import json

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

def list_materials(class_id: int, subject: str | None = None):
    with get_conn() as conn:
        sql = ("SELECT m.*, c.name AS class_name, u.username AS uploader_name "
               "FROM materials m "
               "JOIN classes c ON m.class_id = c.id "
               "JOIN users u ON m.uploader_id = u.id "
               "WHERE m.class_id = %s")
        params: list = [class_id]
        if subject:
            sql += " AND m.subject = %s"
            params.append(subject)
        sql += " ORDER BY m.created_at DESC"
        rows = _rows(conn, sql, tuple(params))
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
# Lecture notes (讲义) — class-scoped like every other domain read
# ---------------------------------------------------------------------------

def list_lecture_notes(class_id: int):
    """Lecture notes of one class. Same boundary rule as materials: the class_id
    comes from the token, never from the query string."""
    with get_conn() as conn:
        rows = _rows(conn,
            "SELECT id, class_id, title FROM lecture_notes WHERE class_id = %s ORDER BY id",
            (class_id,),
        )
        return [dict(r) for r in rows]


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


def get_chunks_by_ids_and_class(chunk_ids: list[int], class_id: int,
                                material_ids: list[int] | None = None,
                                subject_keyword: str | None = None):
    """回表时必须再次核对 class_id — 即使 Qdrant 漏加过滤也能排除他班记录 (spec line 87).

    提示词 E：可选再按「智能体绑定的材料」与「学科词」收窄 —— 空 material_ids
    等价于全班（不过滤），subject_keyword 非空时只保留 materials.subject 命中者。
    """
    if not chunk_ids:
        return []
    with get_conn() as conn:
        placeholders = ",".join(["%s"] * len(chunk_ids))
        sql = (f"SELECT kc.*, m.title AS material_title "
               f"FROM knowledge_chunks kc "
               f"JOIN materials m ON m.id = kc.material_id "
               f"WHERE kc.id IN ({placeholders}) AND kc.class_id = %s")
        params: list = [*chunk_ids, class_id]
        if material_ids:
            ph = ",".join(["%s"] * len(material_ids))
            sql += f" AND kc.material_id IN ({ph})"
            params.extend(int(x) for x in material_ids)
        if subject_keyword:
            sql += " AND m.subject = %s"
            params.append(subject_keyword)
        rows = _rows(conn, sql, params)
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Keyword search — MySQL FULLTEXT with ngram parser
# ---------------------------------------------------------------------------

def keyword_search(query: str, class_id: int, top_k: int = 20,
                   material_ids: list[int] | None = None,
                   subject_keyword: str | None = None):
    """MySQL FULLTEXT only; does NOT call embed service or Qdrant (spec line 51-53).

    提示词 E：可在班级边界内再按智能体绑定的 material_ids 与学科词收窄。
    """
    with get_conn() as conn:
        # NATURAL LANGUAGE MODE works with ngram parser for Chinese tokenization.
        # BOOLEAN MODE does NOT support WITH QUERY EXPANSION — that's only for NATURAL LANGUAGE.
        sql = ("SELECT kc.*, m.title AS material_title, "
               "MATCH(kc.chunk_text) AGAINST(%s IN NATURAL LANGUAGE MODE) AS relevance "
               "FROM knowledge_chunks kc "
               "JOIN materials m ON m.id = kc.material_id "
               "WHERE kc.class_id = %s AND kc.index_status = 'ready' "
               "AND MATCH(kc.chunk_text) AGAINST(%s IN NATURAL LANGUAGE MODE) ")
        params: list = [query, class_id, query]
        if material_ids:
            ph = ",".join(["%s"] * len(material_ids))
            sql += f" AND kc.material_id IN ({ph})"
            params.extend(int(x) for x in material_ids)
        if subject_keyword:
            sql += " AND m.subject = %s"
            params.append(subject_keyword)
        sql += " ORDER BY relevance DESC LIMIT %s"
        params.append(top_k)
        rows = _rows(conn, sql, tuple(params))
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Assistant config (lesson 5) — prompt and skill switch, both keyed by subject
# ---------------------------------------------------------------------------

def get_assistant_prompt(subject: str):
    """Return the configured prompt for a subject, or None when unset."""
    with get_conn() as conn:
        row = _one(conn, "SELECT prompt FROM assistant_prompts WHERE subject = %s", (subject,))
        return row["prompt"] if row else None


def get_assistant_skill_enabled(subject: str, skill_name: str) -> bool:
    """Return True iff the skill is switched on for that subject (missing row = off)."""
    with get_conn() as conn:
        row = _one(conn,
            "SELECT enabled FROM assistant_skills WHERE subject = %s AND skill_name = %s",
            (subject, skill_name),
        )
        return bool(row["enabled"]) if row else False


def list_assistant_prompts():
    """All per-subject prompts, ordered by subject (teacher config screen)."""
    with get_conn() as conn:
        rows = _rows(conn,
            "SELECT subject, prompt, updated_at FROM assistant_prompts ORDER BY subject")
        return [dict(r) for r in rows]


def upsert_assistant_prompt(subject: str, prompt: str) -> None:
    """Create or overwrite the prompt for a subject (UNIQUE(subject) drives the upsert)."""
    with get_conn() as conn:
        _exec(conn,
            "INSERT INTO assistant_prompts (subject, prompt) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE prompt = VALUES(prompt)",
            (subject, prompt))


def list_assistant_skills():
    """All subject/skill switches (teacher config screen)."""
    with get_conn() as conn:
        rows = _rows(conn,
            "SELECT subject, skill_name, enabled FROM assistant_skills "
            "ORDER BY subject, skill_name")
        return [dict(r) for r in rows]


def upsert_assistant_skill(subject: str, skill_name: str, enabled: bool) -> None:
    """Create or flip one switch — UNIQUE(subject, skill_name) drives the upsert."""
    with get_conn() as conn:
        _exec(conn,
            "INSERT INTO assistant_skills (subject, skill_name, enabled) VALUES (%s, %s, %s) "
            "ON DUPLICATE KEY UPDATE enabled = VALUES(enabled)",
            (subject, skill_name, 1 if enabled else 0))


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


# ---------------------------------------------------------------------------
# Homework flow (lesson 6) — assignments / submissions / grades / wrong_questions
# Every read that a route exposes is scoped by a class_id (or a student_id) that
# the caller took from the JWT; never from the query string or the body.
# ---------------------------------------------------------------------------

def create_assignment(class_id: int, teacher_id: int, title: str, body: str,
                      subject: str, deadline) -> int:
    with get_conn() as conn:
        cur = _exec(conn,
            "INSERT INTO assignments (class_id, teacher_id, title, body, subject, deadline) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (class_id, teacher_id, title, body, subject, deadline),
        )
        return cur.lastrowid


def list_assignments(class_id: int):
    """This class' assignments, newest first."""
    with get_conn() as conn:
        rows = _rows(conn,
            "SELECT id, class_id, teacher_id, title, body, subject, deadline, status, created_at "
            "FROM assignments WHERE class_id = %s ORDER BY created_at DESC, id DESC",
            (class_id,),
        )
        return [dict(r) for r in rows]


def get_assignment(assignment_id: int, class_id: int):
    """Class-scoped single assignment — cross-class and missing both return None."""
    with get_conn() as conn:
        row = _one(conn,
            "SELECT id, class_id, teacher_id, title, body, subject, deadline, status, created_at "
            "FROM assignments WHERE id = %s AND class_id = %s",
            (assignment_id, class_id),
        )
        return dict(row) if row else None


def find_submission(assignment_id: int, student_id: int):
    """The one submission this student may have for this assignment (UNIQUE pair)."""
    with get_conn() as conn:
        row = _one(conn,
            "SELECT id, status FROM submissions WHERE assignment_id = %s AND student_id = %s",
            (assignment_id, student_id),
        )
        return dict(row) if row else None


def create_submission(assignment_id: int, student_id: int, class_id: int, answer_text: str) -> int:
    with get_conn() as conn:
        cur = _exec(conn,
            "INSERT INTO submissions (assignment_id, student_id, class_id, answer_text, status) "
            "VALUES (%s, %s, %s, %s, %s)",
            (assignment_id, student_id, class_id, answer_text, "pending"),
        )
        return cur.lastrowid


def get_submission(submission_id: int):
    """Single submission WITHOUT a class filter — the route decides who may see it
    (student: only own; teacher: only own class) and answers 404 either way."""
    with get_conn() as conn:
        row = _one(conn,
            "SELECT id, assignment_id, student_id, class_id, answer_text, status, error_msg, "
            "submitted_at, graded_at FROM submissions WHERE id = %s",
            (submission_id,),
        )
        return dict(row) if row else None


# 提交列表（第 6 课前端「交作业 / 作业与改分」页面需要）：
# 学生只看本人提交，教师只看本班提交 —— class_id / student_id 一律取自 JWT。
# LEFT JOIN grades 把 AI 分与教师终评一并带回，避免前端逐条回表。
_SUBMISSION_LIST_SELECT = (
    "SELECT s.id, s.assignment_id, s.student_id, s.class_id, s.answer_text, "
    "s.status, s.error_msg, s.submitted_at, s.graded_at, "
    "a.title AS assignment_title, a.subject, a.deadline, "
    "u.username AS student_name, "
    "g.ai_score, g.ai_comment, g.ai_citations, g.final_score, g.final_comment, g.graded_by "
    "FROM submissions s "
    "JOIN assignments a ON a.id = s.assignment_id "
    "JOIN users u ON u.id = s.student_id "
    "LEFT JOIN grades g ON g.submission_id = s.id "
)


def list_submissions_for_student(student_id: int):
    """一人某一学生名下的全部提交（跨所有作业），新的在前。"""
    with get_conn() as conn:
        rows = _rows(conn,
            _SUBMISSION_LIST_SELECT + "WHERE s.student_id = %s "
            "ORDER BY s.submitted_at DESC, s.id DESC",
            (student_id,),
        )
        return [dict(r) for r in rows]


def list_submissions_for_class(class_id: int):
    """某班（教师视角）的全部提交，新的在前。"""
    with get_conn() as conn:
        rows = _rows(conn,
            _SUBMISSION_LIST_SELECT + "WHERE s.class_id = %s "
            "ORDER BY s.submitted_at DESC, s.id DESC",
            (class_id,),
        )
        return [dict(r) for r in rows]


def mark_submission(submission_id: int, status: str, error_msg=None, graded_at=None) -> None:
    """Move the grading state machine (pending → processing → done / failed)."""
    with get_conn() as conn:
        _exec(conn,
            "UPDATE submissions SET status = %s, error_msg = %s, graded_at = %s WHERE id = %s",
            (status, error_msg, graded_at, submission_id))


def get_grade(submission_id: int):
    """AI grade + teacher override for a submission, or None while grading is undone."""
    with get_conn() as conn:
        row = _one(conn,
            "SELECT submission_id, ai_score, ai_comment, ai_citations, final_score, "
            "final_comment, graded_by FROM grades WHERE submission_id = %s",
            (submission_id,),
        )
        if not row:
            return None
        data = dict(row)
        if isinstance(data.get("ai_citations"), (str, bytes)):
            try:
                data["ai_citations"] = json.loads(data["ai_citations"])
            except Exception:
                data["ai_citations"] = []
        return data


def insert_ai_grade(submission_id: int, ai_score: int, ai_comment: str, ai_citations) -> None:
    with get_conn() as conn:
        _exec(conn,
            "INSERT INTO grades (submission_id, ai_score, ai_comment, ai_citations) "
            "VALUES (%s, %s, %s, %s)",
            (submission_id, ai_score, ai_comment,
             json.dumps(ai_citations or [], ensure_ascii=False)),
        )


def update_final_grade(submission_id: int, final_score: int, final_comment: str,
                       graded_by: int) -> int:
    """Teacher override — overwrites, never appends. Returns affected row count."""
    with get_conn() as conn:
        cur = _exec(conn,
            "UPDATE grades SET final_score = %s, final_comment = %s, graded_by = %s "
            "WHERE submission_id = %s",
            (final_score, final_comment, graded_by, submission_id),
        )
        return cur.rowcount


def insert_wrong_question(student_id: int, submission_id: int, class_id: int, subject: str,
                          question_text: str, student_answer: str, wrong_reason: str) -> int:
    with get_conn() as conn:
        cur = _exec(conn,
            "INSERT INTO wrong_questions (student_id, submission_id, class_id, subject, "
            "question_text, student_answer, wrong_reason, status) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (student_id, submission_id, class_id, subject, question_text, student_answer,
             wrong_reason, "pending"),
        )
        return cur.lastrowid


_WRONG_COLS = ("id, student_id, submission_id, class_id, subject, question_text, "
               "student_answer, wrong_reason, explanation, status, created_at")


def list_wrong_questions_by_student(student_id: int):
    with get_conn() as conn:
        rows = _rows(conn,
            f"SELECT {_WRONG_COLS} FROM wrong_questions WHERE student_id = %s "
            "ORDER BY created_at DESC, id DESC",
            (student_id,),
        )
        return [dict(r) for r in rows]


def list_wrong_questions_by_class(class_id: int):
    """Teacher view — the whole class, still class-bounded."""
    with get_conn() as conn:
        rows = _rows(conn,
            f"SELECT {_WRONG_COLS} FROM wrong_questions WHERE class_id = %s "
            "ORDER BY created_at DESC, id DESC",
            (class_id,),
        )
        return [dict(r) for r in rows]


def get_wrong_question(wrong_id: int):
    """No filter here either: the route applies the ownership/class rule and 404s."""
    with get_conn() as conn:
        row = _one(conn,
            f"SELECT {_WRONG_COLS} FROM wrong_questions WHERE id = %s",
            (wrong_id,),
        )
        return dict(row) if row else None


def set_wrong_explanation(wrong_id: int, explanation: str) -> None:
    with get_conn() as conn:
        _exec(conn,
            "UPDATE wrong_questions SET explanation = %s, status = %s WHERE id = %s",
            (explanation, "explained", wrong_id))


# ---------------------------------------------------------------------------
# Tutor agents / skills / bindings (提示词 C) — every read is class-scoped.
#   tutor_agents  1─n tutor_agent_skills n─1 skills
#                1─n tutor_agent_materials n─1 materials
# ---------------------------------------------------------------------------

def _agent_skill_ids(conn, agent_id: int) -> list[int]:
    rows = _rows(conn,
        "SELECT skill_id FROM tutor_agent_skills WHERE agent_id = %s ORDER BY skill_id",
        (agent_id,))
    return [int(r["skill_id"]) for r in rows]


def _agent_material_ids(conn, agent_id: int) -> list[int]:
    rows = _rows(conn,
        "SELECT material_id FROM tutor_agent_materials WHERE agent_id = %s ORDER BY material_id",
        (agent_id,))
    return [int(r["material_id"]) for r in rows]


def list_agents(class_id: int, enabled_only: bool = False):
    """本班智能体列表。学生视角只回 enabled=true 的（由路由决定传 True）。"""
    with get_conn() as conn:
        sql = ("SELECT id, class_id, name, subject, prompt, enabled, created_at "
               "FROM tutor_agents WHERE class_id = %s")
        if enabled_only:
            sql += " AND enabled = 1"
        sql += " ORDER BY id"
        agents = [dict(r) for r in _rows(conn, sql, (class_id,))]
        for a in agents:
            a["enabled"] = bool(a["enabled"])
            a["skill_ids"] = _agent_skill_ids(conn, a["id"])
            a["material_ids"] = _agent_material_ids(conn, a["id"])
        return agents


def get_agent(agent_id: int, class_id: int):
    """跨班与不存在同样返回 None（路由据此给同构 404）。"""
    with get_conn() as conn:
        row = _one(conn,
            "SELECT id, class_id, name, subject, prompt, enabled, created_at "
            "FROM tutor_agents WHERE id = %s AND class_id = %s",
            (agent_id, class_id))
        if not row:
            return None
        a = dict(row)
        a["enabled"] = bool(a["enabled"])
        a["skill_ids"] = _agent_skill_ids(conn, a["id"])
        a["material_ids"] = _agent_material_ids(conn, a["id"])
        return a


def get_agent_by_name(name: str, class_id: int):
    with get_conn() as conn:
        row = _one(conn,
            "SELECT id, name FROM tutor_agents WHERE class_id = %s AND name = %s",
            (class_id, name))
        return dict(row) if row else None


def create_agent(class_id: int, name: str, prompt: str, enabled: bool,
                 subject: str | None, skill_ids: list[int], material_ids: list[int]) -> int:
    """主表 + 两张绑定表在同一个事务里写入。"""
    with get_conn() as conn:
        cur = _exec(conn,
            "INSERT INTO tutor_agents (class_id, name, subject, prompt, enabled) "
            "VALUES (%s, %s, %s, %s, %s)",
            (class_id, name, subject, prompt, 1 if enabled else 0))
        agent_id = cur.lastrowid
        for sid in skill_ids:
            _exec(conn, "INSERT INTO tutor_agent_skills (agent_id, skill_id) VALUES (%s, %s)",
                  (agent_id, sid))
        for mid in material_ids:
            _exec(conn, "INSERT INTO tutor_agent_materials (agent_id, material_id) VALUES (%s, %s)",
                  (agent_id, mid))
        return agent_id


def update_agent(agent_id: int, name: str, prompt: str, enabled: bool,
                 subject: str | None, skill_ids: list[int], material_ids: list[int]) -> None:
    """覆盖更新：清掉旧绑定再按新 id 集重建（同一事务）。"""
    with get_conn() as conn:
        _exec(conn,
            "UPDATE tutor_agents SET name = %s, subject = %s, prompt = %s, enabled = %s "
            "WHERE id = %s",
            (name, subject, prompt, 1 if enabled else 0, agent_id))
        _exec(conn, "DELETE FROM tutor_agent_skills WHERE agent_id = %s", (agent_id,))
        _exec(conn, "DELETE FROM tutor_agent_materials WHERE agent_id = %s", (agent_id,))
        for sid in skill_ids:
            _exec(conn, "INSERT INTO tutor_agent_skills (agent_id, skill_id) VALUES (%s, %s)",
                  (agent_id, sid))
        for mid in material_ids:
            _exec(conn, "INSERT INTO tutor_agent_materials (agent_id, material_id) VALUES (%s, %s)",
                  (agent_id, mid))


def delete_agent(agent_id: int) -> None:
    """删智能体；两张绑定表靠外键 ON DELETE CASCADE 自动清空。

    solve_sessions.agent_id 是无级联的外键，直接删父行会被外键挡住 —— 先把
    指向它的会话置空（会话本身保留，只是不再对应任何智能体）。
    """
    with get_conn() as conn:
        _exec(conn, "UPDATE solve_sessions SET agent_id = NULL WHERE agent_id = %s", (agent_id,))
        _exec(conn, "DELETE FROM tutor_agents WHERE id = %s", (agent_id,))


# ---------------------------------------------------------------------------
# Skills（提示词 C）— (class_id, name) 唯一，班级是数据边界。
# ---------------------------------------------------------------------------

def list_skills(class_id: int):
    """本班技能列表，附带每个技能被多少个智能体绑定（删除二次确认用）。"""
    with get_conn() as conn:
        rows = _rows(conn,
            "SELECT s.id, s.class_id, s.name, s.trigger_text, s.subject_text, "
            "s.tools_json, s.enabled, s.description, s.source_text, s.seed_key, s.created_at, "
            "COUNT(tas.skill_id) AS binding_count "
            "FROM skills s "
            "LEFT JOIN tutor_agent_skills tas ON tas.skill_id = s.id "
            "WHERE s.class_id = %s "
            "GROUP BY s.id ORDER BY s.id",
            (class_id,))
        out = []
        for r in rows:
            d = dict(r)
            d["enabled"] = bool(d["enabled"])
            d["binding_count"] = int(d["binding_count"] or 0)
            out.append(d)
        return out


def get_skill(skill_id: int, class_id: int):
    with get_conn() as conn:
        row = _one(conn, "SELECT * FROM skills WHERE id = %s AND class_id = %s",
                   (skill_id, class_id))
        return dict(row) if row else None


def get_skill_by_name(name: str, class_id: int):
    with get_conn() as conn:
        row = _one(conn, "SELECT id, name FROM skills WHERE class_id = %s AND name = %s",
                   (class_id, name))
        return dict(row) if row else None


def create_skill(class_id: int, name: str, trigger_text: str, subject_text: str | None,
                 tools_json: str, enabled: bool, description: str, source_text: str,
                 seed_key: str | None = None) -> int:
    with get_conn() as conn:
        cur = _exec(conn,
            "INSERT INTO skills (class_id, name, trigger_text, subject_text, tools_json, "
            "enabled, description, source_text, seed_key) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (class_id, name, trigger_text, subject_text, tools_json,
             1 if enabled else 0, description, source_text, seed_key))
        return cur.lastrowid


def update_skill(skill_id: int, name: str, trigger_text: str, subject_text: str | None,
                 tools_json: str, enabled: bool, description: str, source_text: str) -> None:
    with get_conn() as conn:
        _exec(conn,
            "UPDATE skills SET name = %s, trigger_text = %s, subject_text = %s, "
            "tools_json = %s, enabled = %s, description = %s, source_text = %s WHERE id = %s",
            (name, trigger_text, subject_text, tools_json, 1 if enabled else 0,
             description, source_text, skill_id))


def delete_skill(skill_id: int) -> None:
    """删技能；tutor_agent_skills 里的绑定靠外键 ON DELETE CASCADE 清空。"""
    with get_conn() as conn:
        _exec(conn, "DELETE FROM skills WHERE id = %s", (skill_id,))


def list_agent_skills(agent_id: int):
    """某智能体绑定的技能对象列表（route_skill 的候选集）。"""
    with get_conn() as conn:
        rows = _rows(conn,
            "SELECT s.id, s.name, s.trigger_text, s.description, s.enabled, "
            "s.subject_text, s.tools_json "
            "FROM tutor_agent_skills tas "
            "JOIN skills s ON s.id = tas.skill_id "
            "WHERE tas.agent_id = %s ORDER BY s.id",
            (agent_id,))
        out = []
        for r in rows:
            d = dict(r)
            d["enabled"] = bool(d["enabled"])
            out.append(d)
        return out


def list_skill_ids_for_class(class_id: int) -> set[int]:
    with get_conn() as conn:
        rows = _rows(conn, "SELECT id FROM skills WHERE class_id = %s", (class_id,))
        return {int(r["id"]) for r in rows}


def list_material_ids_for_class(class_id: int) -> set[int]:
    with get_conn() as conn:
        rows = _rows(conn, "SELECT id FROM materials WHERE class_id = %s", (class_id,))
        return {int(r["id"]) for r in rows}


# ---------------------------------------------------------------------------
# Solve sessions / messages（提示词 F）—— 归属以 user 为边界：
# 列表只回本人会话，读取/写入他班或他人的会话一律当作「不存在」（路由给 404）。
# ---------------------------------------------------------------------------

def create_session(agent_id: int, user_id: int, class_id: int) -> int:
    with get_conn() as conn:
        cur = _exec(conn,
            "INSERT INTO solve_sessions (agent_id, student_id, class_id, status) "
            "VALUES (%s, %s, %s, 'active')",
            (agent_id, user_id, class_id))
        return cur.lastrowid


def get_session(session_id: int, user_id: int):
    """本人会话才返回行；跨用户 / 不存在 / 他班都返回 None（同构 404）。"""
    with get_conn() as conn:
        row = _one(conn,
            "SELECT id, agent_id, student_id, class_id, status, created_at, updated_at "
            "FROM solve_sessions WHERE id = %s AND student_id = %s",
            (session_id, user_id))
        return dict(row) if row else None


def list_sessions(user_id: int):
    """本人会话列表；按最后活动时间倒序（updated_at 每次提问都会刷新）。"""
    with get_conn() as conn:
        rows = _rows(conn,
            "SELECT s.id, s.agent_id, s.student_id, s.class_id, s.status, "
            "s.created_at, s.updated_at, a.name AS agent_name, "
            "(SELECT COUNT(*) FROM solve_messages m WHERE m.session_id = s.id) AS message_count "
            "FROM solve_sessions s "
            "LEFT JOIN tutor_agents a ON a.id = s.agent_id "
            "WHERE s.student_id = %s "
            "ORDER BY s.updated_at DESC, s.id DESC",
            (user_id,))
        out = []
        for r in rows:
            d = dict(r)
            d["message_count"] = int(d.get("message_count") or 0)
            out.append(d)
        return out


def list_session_messages(session_id: int, limit: int = 6):
    """最近 limit 条（按 id 倒序取），返回时反转成时间正序。"""
    with get_conn() as conn:
        rows = _rows(conn,
            "SELECT id, session_id, role, content, created_at FROM solve_messages "
            "WHERE session_id = %s ORDER BY id DESC LIMIT %s",
            (session_id, max(1, int(limit))))
        return [dict(r) for r in reversed(rows)]


def add_session_message(session_id: int, role: str, content: str) -> int:
    with get_conn() as conn:
        cur = _exec(conn,
            "INSERT INTO solve_messages (session_id, role, content) VALUES (%s, %s, %s)",
            (session_id, role, content))
        return cur.lastrowid


def touch_session(session_id: int) -> None:
    """刷新 updated_at，让会话列表按「最后消息时间」倒序。"""
    with get_conn() as conn:
        _exec(conn, "UPDATE solve_sessions SET updated_at = NOW() WHERE id = %s", (session_id,))


# ---------------------------------------------------------------------------
# MCP servers / tool cache（提示词 B）
#
# auth_token 只允许出现在 get_mcp_server* 的内部读取里（connect / 对话链路要用）；
# 任何面向客户端的序列化都在 app/mcp.py 完成，只给 has_auth 布尔值。
# 班级是数据边界：所有读取都带 class_id。
# ---------------------------------------------------------------------------

def list_mcp_servers(class_id: int):
    """本班服务器列表；不返回 auth_token，只给 has_auth 与 tool_count。"""
    with get_conn() as conn:
        rows = _rows(conn,
            "SELECT s.id, s.class_id, s.name, s.base_url, s.connected, s.created_at, "
            "(s.auth_token <> '') AS has_auth, "
            "(SELECT COUNT(*) FROM mcp_tools t WHERE t.server_id = s.id) AS tool_count "
            "FROM mcp_servers s WHERE s.class_id = %s ORDER BY s.id",
            (class_id,))
        out = []
        for r in rows:
            d = dict(r)
            d["connected"] = bool(d["connected"])
            d["has_auth"] = bool(d["has_auth"])
            d["tool_count"] = int(d["tool_count"] or 0)
            out.append(d)
        return out


def get_mcp_server(server_id: int, class_id: int):
    """跨班 / 不存在 → None（与「不存在」同构）。含 auth_token，仅服务端使用。"""
    with get_conn() as conn:
        row = _one(conn, "SELECT * FROM mcp_servers WHERE id = %s AND class_id = %s",
                   (server_id, class_id))
        return dict(row) if row else None


def get_mcp_server_by_name(name: str, class_id: int):
    """技能里写的是服务器名，落到本班登记记录（含 auth_token）。"""
    with get_conn() as conn:
        row = _one(conn, "SELECT * FROM mcp_servers WHERE class_id = %s AND name = %s",
                   (class_id, name))
        return dict(row) if row else None


def create_mcp_server(class_id: int, name: str, base_url: str, auth_token: str) -> int:
    with get_conn() as conn:
        cur = _exec(conn,
            "INSERT INTO mcp_servers (class_id, name, base_url, auth_token, connected) "
            "VALUES (%s, %s, %s, %s, 0)",
            (class_id, name, base_url, auth_token or ""))
        return cur.lastrowid


def update_mcp_server(server_id: int, name: str, base_url: str,
                      auth_token: str | None = None) -> None:
    """改 name / base_url；auth_token 传 None 表示不改，传非空字符串则替换。"""
    with get_conn() as conn:
        _exec(conn, "UPDATE mcp_servers SET name = %s, base_url = %s WHERE id = %s",
              (name, base_url, server_id))
        if auth_token:
            _exec(conn, "UPDATE mcp_servers SET auth_token = %s WHERE id = %s",
                  (auth_token, server_id))


def delete_mcp_server(server_id: int) -> None:
    """删服务器；mcp_tools 靠外键 ON DELETE CASCADE 清空。"""
    with get_conn() as conn:
        _exec(conn, "DELETE FROM mcp_servers WHERE id = %s", (server_id,))


def save_mcp_discovery(server_id: int, tools: list[dict]) -> None:
    """连接成功：同一事务里刷新工具缓存并置 connected=1。"""
    with get_conn() as conn:
        _exec(conn, "DELETE FROM mcp_tools WHERE server_id = %s", (server_id,))
        for t in tools:
            _exec(conn,
                "INSERT INTO mcp_tools (server_id, name, description) VALUES (%s, %s, %s)",
                (server_id, t["name"], t.get("description") or ""))
        _exec(conn, "UPDATE mcp_servers SET connected = 1 WHERE id = %s", (server_id,))


def set_mcp_disconnected(server_id: int) -> None:
    """断开：同一事务里置 connected=0 并清空工具缓存（此后不可再调用）。"""
    with get_conn() as conn:
        _exec(conn, "UPDATE mcp_servers SET connected = 0 WHERE id = %s", (server_id,))
        _exec(conn, "DELETE FROM mcp_tools WHERE server_id = %s", (server_id,))


def list_mcp_tools(server_id: int):
    with get_conn() as conn:
        rows = _rows(conn,
            "SELECT id, server_id, name, description FROM mcp_tools "
            "WHERE server_id = %s ORDER BY name",
            (server_id,))
        return [dict(r) for r in rows]


def get_mcp_tool(server_id: int, name: str):
    with get_conn() as conn:
        row = _one(conn,
            "SELECT id, server_id, name, description FROM mcp_tools "
            "WHERE server_id = %s AND name = %s",
            (server_id, name))
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# MCP 工具调用审计（提示词 D）
#
# 独立表 mcp_tool_calls：每次 tools/call（含 denied / error）写一行。
# build_stream 跑在请求上下文之外（生成器在视图返回后才迭代），身份由调用方
# 显式传入 user_id，不依赖 request。
# ---------------------------------------------------------------------------

def insert_mcp_tool_call(class_id: int, server_id, server_name: str, tool_name: str,
                         tool_ref: str, status: str, user_id: int) -> int:
    with get_conn() as conn:
        cur = _exec(conn,
            "INSERT INTO mcp_tool_calls "
            "(class_id, server_id, server_name, tool_name, tool_ref, status, user_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (class_id, server_id, server_name, tool_name, tool_ref, status, user_id or 0))
        return cur.lastrowid


def list_mcp_tool_calls(class_id: int, limit: int = 50):
    """本班审计，按调用时间倒序（同一秒内按 id 倒序，稳定）。"""
    with get_conn() as conn:
        rows = _rows(conn,
            "SELECT id, class_id, server_id, server_name, tool_name, tool_ref, "
            "status, user_id, called_at FROM mcp_tool_calls "
            "WHERE class_id = %s ORDER BY called_at DESC, id DESC LIMIT %s",
            (class_id, max(1, int(limit))))
    out = []
    for r in rows:
        d = dict(r)
        d["called_at"] = d["called_at"].isoformat() if d.get("called_at") else None
        out.append(d)
    return out
