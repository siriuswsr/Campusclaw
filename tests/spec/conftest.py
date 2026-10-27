"""作业流程（lesson 6）规约测试的共享 fixtures。

运行方式：`make check` → 在 app 容器内 `pytest tests/ -v`。

为什么在容器内跑：
  1) MySQL / Qdrant 只在 compose 网络内可达，宿主机没有端口映射；
  2) 进程内才能 monkeypatch 批改 / 讲解的网关调用（`app.grader.call_chat`、
     `app.wrong.call_chat`），把 21 个 Scenario 变成确定性、不依赖外网的测试。
真实网关的端到端验证由 `make eval` 与 `tests/acceptance.py` 负责。
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import create_app, db          # noqa: E402
from app import grader, wrong           # noqa: E402
from app.security import hash_password  # noqa: E402

TEACHER_PW = "teacher123"
STUDENT_PW = "student123"


# ---------------------------------------------------------------------------
# App / client / 身份
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def app():
    return create_app()


@pytest.fixture(scope="session")
def client(app):
    """真实 Flask 测试客户端 —— 走完整路由 / 装饰器 / 数据库链路。"""
    return app.test_client()


def _login(client, username, password):
    r = client.post("/api/login", json={"username": username, "password": password})
    assert r.status_code == 200, (
        f"login {username}: {r.status_code} {r.get_data(as_text=True)}")
    return r.get_json()["token"]


@pytest.fixture(scope="session")
def teacher_b_ready(app):
    """种子只建了 A 班教师；跨班用例需要一名 B 班教师。幂等写入。"""
    with db.get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM classes WHERE name = %s", ("班级 B",))
        row = cur.fetchone()
        assert row, "缺少「班级 B」—— 先让容器跑一次 scripts/init_db.py"
        cur.execute(
            "INSERT INTO users (username, password_hash, role, class_id) "
            "VALUES (%s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE password_hash = VALUES(password_hash), "
            "role = VALUES(role), class_id = VALUES(class_id)",
            ("teacher_b", hash_password(TEACHER_PW), "teacher", row["id"]),
        )
    return "teacher_b"


@pytest.fixture(scope="session")
def teacher_a_token(client):
    return _login(client, "teacher_a", TEACHER_PW)


@pytest.fixture(scope="session")
def teacher_b_token(client, teacher_b_ready):
    return _login(client, "teacher_b", TEACHER_PW)


@pytest.fixture(scope="session")
def student_a1_token(client):
    return _login(client, "student_a1", STUDENT_PW)


@pytest.fixture(scope="session")
def student_b1_token(client):
    return _login(client, "student_b1", STUDENT_PW)


@pytest.fixture(scope="session")
def auth():
    def _headers(token):
        return {"Authorization": f"Bearer {token}"}
    return _headers


# ---------------------------------------------------------------------------
# 网关桩 —— 默认屏蔽外网，让批改 / 讲解确定性通过
# ---------------------------------------------------------------------------

def grade_reply(score, comment="答题规范，步骤完整。", wrong_reason="", citations=None):
    """返回一个假的 call_chat：输出批改 skill 约定的 JSON。"""
    def _fake(messages, timeout=30):
        return json.dumps({
            "score": score,
            "comment": comment,
            "wrong_reason": wrong_reason,
            "citations": citations or [],
        }, ensure_ascii=False)
    return _fake


DEFAULT_EXPLANATION = (
    "1) 错因：判断单调性时把对称轴的位置看错了。\n\n"
    "2) 思路：先求对称轴 x = -b/(2a)，再在给定区间上任取 x1 < x2 作差 "
    "f(x1) - f(x2)，由差的符号判断走向。\n\n"
    "3) 讲义出处：《二次函数的图象与性质》"
)


def explain_reply(text=DEFAULT_EXPLANATION, timeout=None):
    def _fake(messages, timeout=30):
        return json.dumps({"explanation": text}, ensure_ascii=False)
    return _fake


@pytest.fixture(autouse=True)
def stub_gateways(monkeypatch):
    """所有用例默认不碰真实网关：批改给 85 分（通过），讲解给固定三段。

    需要别的网关行为（超时 / 低分）的用例，在测试体里再次 monkeypatch 同名
    属性即可 —— 函数级 monkeypatch 是同一个实例，后设置的覆盖先前的。
    """
    monkeypatch.setattr(grader, "call_chat", grade_reply(85, "思路正确，计算无误。"))
    monkeypatch.setattr(wrong, "call_chat", explain_reply())


# ---------------------------------------------------------------------------
# 便捷操作 fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def publish(client, auth):
    """发布一条本班作业 → assignment_id。days<0 造过期作业。"""
    def _publish(token, subject="math", body="解方程 x^2-5x+6=0", days=7, title="练习"):
        deadline = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
        r = client.post("/api/assignments", json={
            "title": title, "body": body, "subject": subject, "deadline": deadline,
        }, headers=auth(token))
        assert r.status_code == 201, r.get_data(as_text=True)
        return r.get_json()["assignment_id"]
    return _publish


@pytest.fixture()
def submit(client, auth):
    def _submit(token, assignment_id, answer="x=2 或 x=3"):
        return client.post("/api/submissions", json={
            "assignment_id": assignment_id, "answer_text": answer,
        }, headers=auth(token))
    return _submit


@pytest.fixture()
def wait_status(client, auth):
    """轮询 GET /api/submissions/<sid> 直到目标状态。"""
    def _wait(token, sid, want, timeout=20.0):
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            last = client.get(f"/api/submissions/{sid}", headers=auth(token)).get_json()
            if last.get("status") == want:
                return last
            time.sleep(0.2)
        raise AssertionError(f"status 未变为 {want!r}，最后一次={last}")
    return _wait


# ---------------------------------------------------------------------------
# DB 断言小工具
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def audit_rows():
    def _rows(action, target_id=None):
        with db.get_conn() as conn:
            cur = conn.cursor()
            if target_id is None:
                cur.execute("SELECT * FROM audit_log WHERE action = %s ORDER BY id DESC",
                            (action,))
            else:
                cur.execute(
                    "SELECT * FROM audit_log WHERE action = %s AND target_id = %s "
                    "ORDER BY id DESC", (action, target_id))
            return [dict(r) for r in cur.fetchall()]
    return _rows


@pytest.fixture(scope="session")
def wrong_of_submission():
    def _get(submission_id):
        with db.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM wrong_questions WHERE submission_id = %s",
                        (submission_id,))
            row = cur.fetchone()
            return dict(row) if row else None
    return _get


@pytest.fixture(scope="session")
def user_id():
    def _id(username):
        return db.get_user_by_username(username)["id"]
    return _id


@pytest.fixture(scope="session")
def class_id():
    def _cid(username):
        return db.get_user_by_username(username)["class_id"]
    return _cid


@pytest.fixture()
def graded_wrong(client, auth, publish, submit, wait_status, wrong_of_submission,
                 monkeypatch):
    """造一条「批改判错并已归集」的错题，返回 (wrong_row, submission_id)。

    判错由桩分数 55（< 60）驱动 —— 与 app/grader.py 的归集阈值一致。
    """
    def _make(teacher_token, student_token, score=55):
        aid = publish(teacher_token, body="判断 f(x)=x^2-4x 在 (2,∞) 上的单调性")
        monkeypatch.setattr(grader, "call_chat",
                            grade_reply(score, "未掌握解题方法。",
                                        "学生作答与本班讲义要点的重合度过低"))
        r = submit(student_token, aid, answer="不会做，随便写。")
        assert r.status_code == 201, r.get_data(as_text=True)
        sid = r.get_json()["submission_id"]
        wait_status(student_token, sid, "done")
        row = wrong_of_submission(sid)
        assert row is not None, "判错但没有归集错题"
        return row, sid
    return _make