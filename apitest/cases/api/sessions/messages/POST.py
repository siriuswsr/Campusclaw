"""POST /api/sessions/<id>/messages —— SSE 四类事件

① 建会话 → 提问 → HTTP 200；首帧 event: meta
② 帧名只含 meta / delta / done / error；以 done（带 refs 数组）或 error 收尾
   （网关/向量服务异常时走 error，HTTP 仍为 200）
③ 跨用户：student_b1 向 student_a1 的会话提问 → 404，与不存在的 id 完全一致
"""
import uuid

import requests


def _create_agent(base_url, teacher_headers, enabled=True):
    name = "apitest-msg-" + uuid.uuid4().hex[:8]
    r = requests.post(f"{base_url}/api/agents", headers=teacher_headers,
                      json={"name": name, "enabled": enabled, "prompt": "apitest"},
                      timeout=15)
    return r.json()["id"] if r.status_code == 201 else None


def _drop(db, table, row_id):
    with db.get_conn() as conn:
        db._exec(conn, f"DELETE FROM {table} WHERE id = %s", (row_id,))


def test_session_message_stream_frames(base_url, teacher_headers, student_headers,
                                       sse_reader, db):
    aid = _create_agent(base_url, teacher_headers)
    assert aid is not None
    sid = None
    try:
        sid = requests.post(f"{base_url}/api/sessions", headers=student_headers,
                            json={"agent_id": aid}, timeout=15).json()["id"]

        r = requests.post(f"{base_url}/api/sessions/{sid}/messages",
                          headers=student_headers,
                          json={"question": "分数怎么通分？"},
                          stream=True, timeout=90)
        assert r.status_code == 200, r.text
        events = sse_reader(r)
        r.close()

        assert events, "没有收到任何 SSE 帧"
        names = [e[0] for e in events]
        assert names[0] == "meta", names
        assert set(names) <= {"meta", "delta", "done", "error"}, names

        meta = events[0][1] or {}
        assert meta.get("session_id") == sid, meta
        assert meta.get("agent_id") == aid, meta
        assert "model" in meta, meta

        assert ("done" in names) or ("error" in names), names
        if "done" in names:
            done = [e[1] for e in events if e[0] == "done"][-1] or {}
            assert isinstance(done.get("refs"), list), done

        # 落库核对：本轮 user 消息已写入 solve_messages
        with db.get_conn() as conn:
            rows = db._rows(conn,
                            "SELECT role FROM solve_messages WHERE session_id = %s",
                            (sid,))
        assert any(x["role"] == "user" for x in rows), rows
    finally:
        if sid:
            _drop(db, "solve_sessions", sid)
        requests.delete(f"{base_url}/api/agents/{aid}", headers=teacher_headers, timeout=15)


def test_other_user_session_message_404_identical(base_url, teacher_headers, student_headers,
                                                 other_headers, db):
    aid = _create_agent(base_url, teacher_headers)
    assert aid is not None
    sid = None
    try:
        sid = requests.post(f"{base_url}/api/sessions", headers=student_headers,
                            json={"agent_id": aid}, timeout=15).json()["id"]
        cross = requests.post(f"{base_url}/api/sessions/{sid}/messages", headers=other_headers,
                              json={"question": "hi"}, timeout=15)
        ghost = requests.post(f"{base_url}/api/sessions/99999999/messages", headers=other_headers,
                              json={"question": "hi"}, timeout=15)
        assert cross.status_code == 404, cross.text
        assert ghost.status_code == 404, ghost.text
        assert cross.json() == ghost.json(), (cross.json(), ghost.json())
    finally:
        if sid:
            _drop(db, "solve_sessions", sid)
        requests.delete(f"{base_url}/api/agents/{aid}", headers=teacher_headers, timeout=15)
