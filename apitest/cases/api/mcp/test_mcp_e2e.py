"""MCP 端到端（提示词 E）—— 连接演示 MCP → 技能绑定 formula_hint@… → 学生会话提问
→ 审计出现 status=ok；断开服务器后再问一次 → 审计出现 status=denied。

三层断言：
  ① HTTP 状态码（登记 201 / 连接 200 / 技能 201 / 会话 201 / 提问 200 / 审计 200）
  ② 返回 JSON 字段（SSE meta.tool_calls 的 tool_ref 与 status；
     /api/mcp/tool-calls 的 calls 里 status / server_name / tool_name / tool_ref / user_id 齐全）
  ③ 数据库行（mcp_tools 缓存 2 行；mcp_tool_calls 先 ok 后 denied 各一行）
"""
import os
import uuid

import requests

MCP_TOKEN = os.environ.get("MCP_AUTH_TOKEN", "")
MCP_BASE_URL = os.environ.get("MCP_BASE_URL", "http://mcp-demo:8090")

TRIGGER = "勾股定理"
QUESTION = "勾股定理是什么？"
TOOL_NAME = "formula_hint"


def _name(prefix: str) -> str:
    return prefix + uuid.uuid4().hex[:8]


def _skill_text(name: str, server_name: str) -> str:
    return (
        f"名称：{name}\n"
        f"适用条件：{TRIGGER}\n"
        f"资料：数学\n"
        f"工具：{TOOL_NAME}@{server_name}\n"
        f"启用：是\n"
        f"\n"
        f"命中的同学先自己回忆直角三角形三边关系，再对照工具给出的公式提示核对。"
    )


def _drop(db, table, row_id):
    with db.get_conn() as conn:
        db._exec(conn, f"DELETE FROM {table} WHERE id = %s", (row_id,))


def _ask(base_url, session_id, headers, sse_reader):
    """在会话里提问，返回 (HTTP 状态码, [(event, data), ...])。"""
    resp = requests.post(f"{base_url}/api/sessions/{session_id}/messages",
                         headers=headers, json={"question": QUESTION},
                         stream=True, timeout=120)
    status = resp.status_code
    # 网关逐字/逐词推 delta，帧数可能上百；上限放宽到 1000，确保读到 done。
    events = sse_reader(resp, max_events=1000) if status == 200 else []
    resp.close()
    return status, events


def _meta_calls(events):
    assert events, "没有收到任何 SSE 帧"
    assert events[0][0] == "meta", events[0]
    meta = events[0][1] or {}
    return meta, meta.get("tool_calls") or []


def _calls_desc(calls):
    """按调用时间倒序（同秒内 id 倒序），用于核对「按时间倒序」契约。"""
    for a, b in zip(calls, calls[1:]):
        if (a.get("called_at") or "") < (b.get("called_at") or ""):
            return False
        if a.get("called_at") == b.get("called_at") and a.get("id") < b.get("id"):
            return False
    return True


def test_mcp_tool_call_audited_ok_then_denied(base_url, teacher_headers, student,
                                              student_headers, sse_reader, db, class_id_of):
    assert MCP_TOKEN, ("app 容器未注入 MCP_AUTH_TOKEN；"
                       "请确认 docker-compose 的 app 环境变量（跑测需在容器内）")
    cid = class_id_of("teacher_a")
    uid = int(student["user"]["id"])

    server_name = _name("apitest-e2e-mcp-")
    skill_name = _name("apitest-e2e-技能-")
    agent_name = _name("apitest-e2e-智能体-")
    tool_ref = f"{TOOL_NAME}@{server_name}"

    server_id = skill_id = agent_id = session_id = None
    try:
        # ---------- ① 登记 + 连接演示 MCP ----------
        reg = requests.post(f"{base_url}/api/mcp/servers", headers=teacher_headers,
                            json={"name": server_name, "base_url": MCP_BASE_URL,
                                  "auth_token": MCP_TOKEN}, timeout=30)
        assert reg.status_code == 201, reg.text
        server_id = reg.json()["id"]

        con = requests.post(f"{base_url}/api/mcp/servers/{server_id}/connect",
                            headers=teacher_headers, timeout=30)
        assert con.status_code == 200, con.text

        with db.get_conn() as conn:
            cached = db._rows(conn,
                              "SELECT name FROM mcp_tools WHERE server_id = %s",
                              (server_id,))
        assert len(cached) == 2, cached

        # ---------- ② 保存含 formula_hint@… 的技能 ----------
        skill_resp = requests.post(
            f"{base_url}/api/skills",
            headers={**teacher_headers, "Content-Type": "text/plain; charset=utf-8"},
            data=_skill_text(skill_name, server_name).encode("utf-8"), timeout=15)
        assert skill_resp.status_code == 201, skill_resp.text
        skill_id = skill_resp.json()["id"]

        # ---------- ③ 智能体绑定该技能 ----------
        agent_resp = requests.post(
            f"{base_url}/api/agents", headers=teacher_headers,
            json={"name": agent_name, "prompt": "你是数学学习助手。", "subject": "数学",
                  "enabled": True, "skill_ids": [skill_id]}, timeout=15)
        assert agent_resp.status_code == 201, agent_resp.text
        agent_id = agent_resp.json()["id"]

        # ---------- ④ 学生会话提问 → meta.tool_calls status=ok ----------
        sess = requests.post(f"{base_url}/api/sessions", headers=student_headers,
                             json={"agent_id": agent_id}, timeout=15)
        assert sess.status_code == 201, sess.text
        session_id = sess.json()["id"]

        status, events = _ask(base_url, session_id, student_headers, sse_reader)
        assert status == 200, "SSE 接口 HTTP 状态必须为 200"
        names = [e[0] for e in events]
        assert names[0] == "meta", names
        assert ("done" in names) or ("error" in names), names

        meta, calls = _meta_calls(events)
        entry = next((c for c in calls if c.get("tool_ref") == tool_ref), None)
        assert entry is not None, meta
        assert entry.get("status") == "ok", entry

        # ---------- ⑤ 教师查询审计：一条 ok，字段齐全、按时间倒序 ----------
        audit = requests.get(f"{base_url}/api/mcp/tool-calls?limit=200",
                             headers=teacher_headers, timeout=15)
        assert audit.status_code == 200, audit.text
        body = audit.json()
        assert isinstance(body.get("calls"), list), body
        assert _calls_desc(body["calls"]), "审计未按调用时间倒序"

        ok_row = next((c for c in body["calls"]
                       if c.get("tool_ref") == tool_ref and c.get("status") == "ok"), None)
        assert ok_row is not None, body["calls"]
        for key in ("id", "server_name", "status", "tool_name", "tool_ref", "user_id"):
            assert key in ok_row, ok_row
        assert ok_row["server_name"] == server_name, ok_row
        assert ok_row["tool_name"] == TOOL_NAME, ok_row
        assert int(ok_row["user_id"]) == uid, ok_row
        assert int(ok_row["class_id"]) == cid, ok_row

        with db.get_conn() as conn:
            db_ok = db._one(conn,
                            "SELECT id, status, user_id FROM mcp_tool_calls "
                            "WHERE tool_ref = %s AND status = 'ok' ORDER BY id DESC",
                            (tool_ref,))
        assert db_ok is not None and int(db_ok["user_id"]) == uid, db_ok

        # ---------- ⑥ 断开服务器 → 同一技能调用变 denied（仍写审计） ----------
        disc = requests.post(f"{base_url}/api/mcp/servers/{server_id}/disconnect",
                             headers=teacher_headers, timeout=15)
        assert disc.status_code == 200, disc.text

        status2, events2 = _ask(base_url, session_id, student_headers, sse_reader)
        assert status2 == 200, "SSE 接口 HTTP 状态必须为 200"
        names2 = [e[0] for e in events2]
        assert names2[0] == "meta" and (("done" in names2) or ("error" in names2)), names2
        _, calls2 = _meta_calls(events2)
        entry2 = next((c for c in calls2 if c.get("tool_ref") == tool_ref), None)
        assert entry2 is not None, calls2
        assert entry2.get("status") == "denied", entry2

        audit2 = requests.get(f"{base_url}/api/mcp/tool-calls?limit=200",
                              headers=teacher_headers, timeout=15)
        assert audit2.status_code == 200, audit2.text
        denied_row = next((c for c in audit2.json()["calls"]
                           if c.get("tool_ref") == tool_ref and c.get("status") == "denied"),
                          None)
        assert denied_row is not None, audit2.json()["calls"]
        assert denied_row["server_name"] == server_name, denied_row
        assert int(denied_row["user_id"]) == uid, denied_row

        with db.get_conn() as conn:
            rows = db._rows(conn,
                            "SELECT status FROM mcp_tool_calls "
                            "WHERE tool_ref = %s ORDER BY id", (tool_ref,))
        statuses = [r["status"] for r in rows]
        assert "ok" in statuses and "denied" in statuses, statuses
    finally:
        if session_id:
            _drop(db, "solve_sessions", session_id)
        if agent_id:
            requests.delete(f"{base_url}/api/agents/{agent_id}",
                            headers=teacher_headers, timeout=15)
        if skill_id:
            requests.delete(f"{base_url}/api/skills/{skill_id}",
                            headers=teacher_headers, timeout=15)
        if server_id:
            requests.delete(f"{base_url}/api/mcp/servers/{server_id}",
                            headers=teacher_headers, timeout=15)
        # 本用例产生的审计行也一并清掉，避免污染后续用例的计数型断言。
        with db.get_conn() as conn:
            db._exec(conn, "DELETE FROM mcp_tool_calls WHERE class_id = %s AND tool_ref = %s",
                     (cid, tool_ref))
