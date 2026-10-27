"""POST /api/sessions

① 学生用本班启用智能体建会话 → 201，DB 核对 solve_sessions 行
② agent 不存在 / 跨班 → 404，且两者响应体完全一致（防他班探测）
③ 已停用 agent → 404
④ GET /api/sessions 只见本人；他人会话读取 → 404
⑤ 智能体停用后，已存在会话仍可读取（enabled 不冻结在创建快照）
"""
import uuid

import requests


def _create_agent(base_url, teacher_headers, enabled=True):
    name = "apitest-sess-" + uuid.uuid4().hex[:8]
    r = requests.post(f"{base_url}/api/agents", headers=teacher_headers,
                      json={"name": name, "enabled": enabled, "prompt": "apitest"},
                      timeout=15)
    return r.json()["id"] if r.status_code == 201 else None


def _drop(db, table, row_id):
    with db.get_conn() as conn:
        db._exec(conn, f"DELETE FROM {table} WHERE id = %s", (row_id,))


def test_create_session_ok_and_db(base_url, teacher_headers, student, student_headers,
                                  db, class_id_of):
    cid = class_id_of("student_a1")
    aid = _create_agent(base_url, teacher_headers)
    assert aid is not None
    sid = None
    try:
        r = requests.post(f"{base_url}/api/sessions", headers=student_headers,
                          json={"agent_id": aid}, timeout=15)
        assert r.status_code == 201, r.text
        sid = r.json()["id"]
        with db.get_conn() as conn:
            row = db._one(conn,
                          "SELECT agent_id, student_id, class_id FROM solve_sessions WHERE id = %s",
                          (sid,))
            assert row is not None
            assert int(row["agent_id"]) == aid, row
            assert int(row["student_id"]) == int(student["user"]["id"]), row
            assert int(row["class_id"]) == cid, row
    finally:
        if sid:
            _drop(db, "solve_sessions", sid)
        requests.delete(f"{base_url}/api/agents/{aid}", headers=teacher_headers, timeout=15)


def test_create_session_404_is_identical_for_missing_and_cross_class(
        base_url, teacher_headers, other_headers):
    aid_a = _create_agent(base_url, teacher_headers)   # A 班的智能体
    try:
        # B 班学生用 A 班 agent → 跨班探测
        cross = requests.post(f"{base_url}/api/sessions", headers=other_headers,
                              json={"agent_id": aid_a}, timeout=15)
        # 同一身份用不存在的 id
        ghost = requests.post(f"{base_url}/api/sessions", headers=other_headers,
                              json={"agent_id": 99999999}, timeout=15)
        assert cross.status_code == 404, cross.text
        assert ghost.status_code == 404, ghost.text
        assert cross.json() == ghost.json(), (cross.json(), ghost.json())
    finally:
        requests.delete(f"{base_url}/api/agents/{aid_a}", headers=teacher_headers, timeout=15)


def test_create_session_disabled_agent_404(base_url, teacher_headers, student_headers):
    aid = _create_agent(base_url, teacher_headers, enabled=False)
    assert aid is not None
    try:
        r = requests.post(f"{base_url}/api/sessions", headers=student_headers,
                          json={"agent_id": aid}, timeout=15)
        assert r.status_code == 404, r.text
    finally:
        requests.delete(f"{base_url}/api/agents/{aid}", headers=teacher_headers, timeout=15)


def test_list_sessions_only_own(base_url, teacher_headers, student, student_headers,
                                other_headers, db):
    aid = _create_agent(base_url, teacher_headers)
    sid = None
    try:
        sid = requests.post(f"{base_url}/api/sessions", headers=student_headers,
                            json={"agent_id": aid}, timeout=15).json()["id"]

        mine = requests.get(f"{base_url}/api/sessions", headers=student_headers, timeout=15)
        assert mine.status_code == 200, mine.text
        my_ids = {s["id"] for s in mine.json()["sessions"]}
        assert sid in my_ids, my_ids
        with db.get_conn() as conn:
            rows = db._rows(conn, "SELECT id, student_id FROM solve_sessions")
        owner = {int(r["id"]): int(r["student_id"]) for r in rows}
        assert all(owner.get(i) == int(student["user"]["id"]) for i in my_ids), "看到了他人的会话"

        theirs = requests.get(f"{base_url}/api/sessions", headers=other_headers, timeout=15)
        assert sid not in {s["id"] for s in theirs.json()["sessions"]}, "B 班看到了 A 班会话"

        # 直接读取他人会话 → 404
        peek = requests.get(f"{base_url}/api/sessions/{sid}", headers=other_headers, timeout=15)
        assert peek.status_code == 404, peek.text
    finally:
        if sid:
            _drop(db, "solve_sessions", sid)
        requests.delete(f"{base_url}/api/agents/{aid}", headers=teacher_headers, timeout=15)


def test_session_readable_after_agent_disabled(base_url, teacher_headers, student_headers,
                                               db, class_id_of):
    aid = _create_agent(base_url, teacher_headers, enabled=True)
    assert aid is not None
    sid = None
    try:
        sid = requests.post(f"{base_url}/api/sessions", headers=student_headers,
                            json={"agent_id": aid}, timeout=15).json()["id"]
        # 老师停用智能体
        upd = requests.put(f"{base_url}/api/agents/{aid}", headers=teacher_headers,
                           json={"name": "apitest-sess-disabled",
                                 "enabled": False, "prompt": "apitest"}, timeout=15)
        assert upd.status_code == 200, upd.text
        # 已存在的会话仍可读取（enabled 不冻结在创建时刻的快照）
        got = requests.get(f"{base_url}/api/sessions/{sid}", headers=student_headers, timeout=15)
        assert got.status_code == 200, got.text
        assert got.json()["session"]["agent_id"] == aid
    finally:
        if sid:
            _drop(db, "solve_sessions", sid)
        requests.delete(f"{base_url}/api/agents/{aid}", headers=teacher_headers, timeout=15)
