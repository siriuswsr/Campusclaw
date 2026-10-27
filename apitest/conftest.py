"""apitest 全局 fixture —— 一键跑测（提示词 I）。

运行位置：MySQL / Qdrant 只在 compose 网络内可达（宿主机没有 3306 / 6333 端口映射），
所以这组用例必须**在 app 容器内**执行（`make apitest` 已封装）。BASE_URL 指向
http://localhost:8080 —— gunicorn 在容器内监听 0.0.0.0:8080，容器里用 localhost 即可。

提供：
  base_url / teacher / student / other_student   （登录后的 {token, user}）
  teacher_headers / student_headers / other_headers
  teacher_creds / student_creds / other_creds    （(username, password)）
  db / class_id_of                               （直连 MySQL 核对，本项目无 ORM，用 pymysql）
  sse_reader                                     （把 SSE 响应解成 [(event, data), ...]）
"""
import json
import os
import pathlib
import sys

import pytest
import requests

# 让用例内 `from app import ...` 可导入：容器内仓库根是 /app。
ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8080")

TEACHER_CREDS = (os.environ.get("TEACHER_USER", "teacher_a"),
                 os.environ.get("TEACHER_PASS", "teacher123"))
STUDENT_CREDS = (os.environ.get("STUDENT_USER", "student_a1"),
                 os.environ.get("STUDENT_PASS", "student123"))
OTHER_CREDS = (os.environ.get("OTHER_USER", "student_b1"),
               os.environ.get("OTHER_PASS", "student123"))


def _login(username: str, password: str) -> dict:
    r = requests.post(f"{BASE_URL}/api/login",
                      json={"username": username, "password": password}, timeout=15)
    assert r.status_code == 200, f"login {username} failed: {r.status_code} {r.text}"
    return r.json()


def _headers(payload: dict) -> dict:
    return {"Authorization": "Bearer " + payload["token"]}


@pytest.fixture(scope="session")
def base_url():
    return BASE_URL


@pytest.fixture(scope="session")
def teacher_creds():
    return TEACHER_CREDS


@pytest.fixture(scope="session")
def student_creds():
    return STUDENT_CREDS


@pytest.fixture(scope="session")
def other_creds():
    return OTHER_CREDS


@pytest.fixture(scope="session")
def teacher():
    return _login(*TEACHER_CREDS)


@pytest.fixture(scope="session")
def student():
    return _login(*STUDENT_CREDS)


@pytest.fixture(scope="session")
def other_student():
    return _login(*OTHER_CREDS)


@pytest.fixture(scope="session")
def teacher_headers(teacher):
    return _headers(teacher)


@pytest.fixture(scope="session")
def student_headers(student):
    return _headers(student)


@pytest.fixture(scope="session")
def other_headers(other_student):
    return _headers(other_student)


@pytest.fixture(scope="session")
def db():
    """直连 MySQL 的核对入口（app.db 提供 get_conn / _one / _rows / _exec）。"""
    from app import db as dbmod
    return dbmod


@pytest.fixture(scope="session")
def class_id_of():
    """username → class_id（直连 DB 取，用于核对隔离边界）。"""
    from app import db as dbmod

    def _cid(username: str):
        with dbmod.get_conn() as conn:
            row = dbmod._one(conn, "SELECT class_id FROM users WHERE username = %s",
                             (username,))
        return int(row["class_id"]) if row else None
    return _cid


@pytest.fixture(scope="session")
def sse_reader():
    """把 requests 的流式响应解成 [(event, data), ...]。

    自己按 UTF-8 解码（网关常不带 charset，requests 会退回 ISO-8859-1 把中文弄乱）。
    """
    def read(resp, max_events: int = 400):
        events = []
        ev = None
        data_lines = []
        for raw in resp.iter_lines(decode_unicode=False):
            if raw is None:
                continue
            line = raw.decode("utf-8", errors="replace")
            if line == "":
                if ev is not None or data_lines:
                    payload = "\n".join(data_lines)
                    try:
                        data = json.loads(payload) if payload else None
                    except Exception:
                        data = None
                    events.append((ev or "message", data))
                    ev, data_lines = None, []
                    if len(events) >= max_events:
                        break
                continue
            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                ev = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:"):].lstrip(" "))
        return events

    return read
