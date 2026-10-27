"""MCP 服务器管理（提示词 E）—— 登记 → 列表 → 连接 → 查看工具 → 断开 → 删除。

三层断言（每个阶段都核对）：
  ① HTTP 状态码
  ② 返回 JSON 字段（servers 里 has_auth=true，且任何响应都不含 auth_token）
  ③ 直连 MySQL 的数据库行（mcp_servers / mcp_tools）

另附：student_a1 调登记接口 → 403，且数据库里不产生该服务器记录。
"""
import os
import uuid

import requests

MCP_TOKEN = os.environ.get("MCP_AUTH_TOKEN", "")
MCP_BASE_URL = os.environ.get("MCP_BASE_URL", "http://mcp-demo:8090")
TOOL_NAMES = {"formula_hint", "unit_convert"}


def _name() -> str:
    return "apitest-mcp-" + uuid.uuid4().hex[:8]


def _post(base_url, path, headers, json=None):
    return requests.post(f"{base_url}{path}", headers=headers, json=json, timeout=30)


def _drop_server(db, server_id):
    with db.get_conn() as conn:
        db._exec(conn, "DELETE FROM mcp_servers WHERE id = %s", (server_id,))


def test_mcp_server_full_flow(base_url, teacher_headers, db, class_id_of):
    assert MCP_TOKEN, ("app 容器未注入 MCP_AUTH_TOKEN；"
                       "请确认 docker-compose 的 app 环境变量（跑测需在容器内）")
    cid = class_id_of("teacher_a")
    name = _name()
    server_id = None
    try:
        # ---------- ① 登记 ----------
        r = _post(base_url, "/api/mcp/servers", teacher_headers,
                  {"name": name, "base_url": MCP_BASE_URL, "auth_token": MCP_TOKEN})
        assert r.status_code == 201, r.text
        body = r.json()
        server_id = body["id"]
        assert body["server"]["has_auth"] is True, body
        assert "auth_token" not in r.text, "登记响应泄露了 auth_token"
        assert "auth_token" not in body["server"], body["server"]

        # ---------- ② 列表 ----------
        lst = requests.get(f"{base_url}/api/mcp/servers", headers=teacher_headers, timeout=15)
        assert lst.status_code == 200, lst.text
        assert "auth_token" not in lst.text, "列表响应泄露了 auth_token"
        row = next((s for s in lst.json()["servers"] if s["id"] == server_id), None)
        assert row is not None, lst.json()
        assert row["name"] == name and row["has_auth"] is True, row
        assert row["connected"] is False and row["tool_count"] == 0, row

        # ---------- ③ 数据库：服务器行归属本班、未连接 ----------
        with db.get_conn() as conn:
            dbro = db._one(conn,
                           "SELECT class_id, name, base_url, connected FROM mcp_servers "
                           "WHERE id = %s", (server_id,))
        assert dbro is not None, "mcp_servers 未落库"
        assert int(dbro["class_id"]) == cid, dbro
        assert dbro["name"] == name and dbro["base_url"] == MCP_BASE_URL, dbro
        assert int(dbro["connected"]) == 0, dbro

        # ---------- ④ 连接（initialize + tools/list → 工具缓存） ----------
        conn_resp = _post(base_url, f"/api/mcp/servers/{server_id}/connect", teacher_headers)
        assert conn_resp.status_code == 200, conn_resp.text
        conn_body = conn_resp.json()
        assert conn_body["connected"] is True, conn_body
        discovered = {t["name"] for t in conn_body["tools"]}
        assert discovered == TOOL_NAMES, conn_body

        with db.get_conn() as conn:
            tool_rows = db._rows(conn,
                                 "SELECT name FROM mcp_tools WHERE server_id = %s",
                                 (server_id,))
            connected = db._one(conn,
                                "SELECT connected FROM mcp_servers WHERE id = %s",
                                (server_id,))
        assert {t["name"] for t in tool_rows} == TOOL_NAMES, tool_rows
        assert len(tool_rows) == 2, tool_rows
        assert int(connected["connected"]) == 1, connected

        # ---------- ⑤ 查看工具 ----------
        tools = requests.get(f"{base_url}/api/mcp/servers/{server_id}/tools",
                             headers=teacher_headers, timeout=15)
        assert tools.status_code == 200, tools.text
        tbody = tools.json()
        assert tbody["connected"] is True and tbody["server_name"] == name, tbody
        assert {t["name"] for t in tbody["tools"]} == TOOL_NAMES, tbody

        # ---------- ⑥ 断开（置 connected=false 并清空缓存） ----------
        disc = _post(base_url, f"/api/mcp/servers/{server_id}/disconnect", teacher_headers)
        assert disc.status_code == 200, disc.text
        dbody = disc.json()
        assert dbody["connected"] is False and dbody["tools"] == [], dbody

        with db.get_conn() as conn:
            left = db._rows(conn,
                            "SELECT id FROM mcp_tools WHERE server_id = %s", (server_id,))
            connected = db._one(conn,
                                "SELECT connected FROM mcp_servers WHERE id = %s",
                                (server_id,))
        assert len(left) == 0, "断开后工具缓存未清空"
        assert int(connected["connected"]) == 0, connected

        after = requests.get(f"{base_url}/api/mcp/servers/{server_id}/tools",
                             headers=teacher_headers, timeout=15)
        assert after.status_code == 200, after.text
        assert after.json()["connected"] is False and after.json()["tools"] == [], after.json()

        # ---------- ⑦ 删除 ----------
        dele = requests.delete(f"{base_url}/api/mcp/servers/{server_id}",
                               headers=teacher_headers, timeout=15)
        assert dele.status_code == 200, dele.text
        assert dele.json() == {"ok": True, "id": server_id}, dele.json()

        with db.get_conn() as conn:
            gone = db._one(conn, "SELECT id FROM mcp_servers WHERE id = %s", (server_id,))
        assert gone is None, "删除后 mcp_servers 仍有该行"
        server_id = None
    finally:
        if server_id:
            _drop_server(db, server_id)


def test_student_register_forbidden(base_url, student_headers, db, class_id_of):
    """非教师登记 → 403，且不产生任何写入。"""
    cid = class_id_of("student_a1")
    name = _name()
    r = _post(base_url, "/api/mcp/servers", student_headers,
              {"name": name, "base_url": MCP_BASE_URL, "auth_token": MCP_TOKEN})
    assert r.status_code == 403, r.text

    with db.get_conn() as conn:
        row = db._one(conn,
                      "SELECT id FROM mcp_servers WHERE class_id = %s AND name = %s",
                      (cid, name))
    assert row is None, "学生登记被拒后数据库却出现了记录"
