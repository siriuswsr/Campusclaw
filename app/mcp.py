"""MCP 服务器管理 + JSON-RPC 客户端（提示词 B/C）。

    GET    /api/mcp/servers                    列出本班 MCP 服务器
    POST   /api/mcp/servers                    登记（name / base_url / auth_token）
    PUT    /api/mcp/servers/<id>               修改（name / base_url）
    DELETE /api/mcp/servers/<id>               删除
    POST   /api/mcp/servers/<id>/connect       连接并发现工具
    POST   /api/mcp/servers/<id>/disconnect    断开并清空工具缓存
    GET    /api/mcp/servers/<id>/tools         查看已发现的工具
    GET    /api/mcp/tool-calls                 MCP 工具调用审计

两条硬约束：
1. auth_token 只存服务端 —— 任何响应都不带回，只给 has_auth 布尔值。
2. 写接口仅教师（非教师 403）；class_id 一律取自 JWT；跨班访问与「不存在」同构 404。
"""
import os
import re
from functools import wraps

import requests
from flask import Blueprint, jsonify, request

from . import db
from .auth import get_current_user, require_auth
from .openapi import doc

mcp_bp = Blueprint("mcp", __name__, url_prefix="/api")

PROTOCOL_VERSION = "2024-11-05"
CLIENT_INFO = {"name": "campusclaw-app", "version": "1.0.0"}

MCP_CONNECT_TIMEOUT = float(os.environ.get("MCP_CONNECT_TIMEOUT", "5"))
MCP_CALL_TIMEOUT = float(os.environ.get("MCP_CALL_TIMEOUT", "5"))

# 服务器名允许中文/字母/数字/下划线/点/连字符；工具名按提示词 A：小写字母开头，
# 可含下划线，无连字符。
TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
SERVER_NAME_RE = re.compile(r"^[^\s@]+$")
SCHEME_RE = re.compile(r"^https?://", re.I)

# OpenAPI 组件（局部 schema，供 8 个接口的 responses 引用）。
SERVER_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "integer"},
        "name": {"type": "string"},
        "base_url": {"type": "string"},
        "connected": {"type": "boolean"},
        "has_auth": {"type": "boolean", "description": "是否已保存令牌（令牌本身永不返回）"},
        "tool_count": {"type": "integer"},
    },
}

TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "description": {"type": "string"},
    },
}

CALL_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "integer"},
        "class_id": {"type": "integer"},
        "server_id": {"type": "integer", "nullable": True},
        "server_name": {"type": "string"},
        "tool_name": {"type": "string"},
        "tool_ref": {"type": "string", "description": "工具名@服务器名"},
        "status": {"type": "string", "enum": ["ok", "denied", "error"]},
        "user_id": {"type": "integer"},
        "called_at": {"type": "string", "format": "date-time"},
    },
}


class McpError(Exception):
    """远端 MCP 调用失败（不可达 / 401 / 非 JSON / JSON-RPC error / 工具报错）。"""


def _err(message: str, status: int):
    return jsonify({"error": message, "message": message}), status


def teacher_only(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        user = get_current_user() or {}
        if user.get("role") != "teacher":
            return _err("仅教师可访问此接口", 403)
        return view(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# JSON-RPC 2.0 客户端（主应用 → 自建 mcp-server）
# ---------------------------------------------------------------------------

def _endpoint(base_url: str) -> str:
    """base_url 填 http://mcp-demo:8090；若已带 /mcp 则原样使用。"""
    url = (base_url or "").strip().rstrip("/")
    if not url:
        raise McpError("服务器地址为空")
    return url if url.endswith("/mcp") else url + "/mcp"


def _rpc(base_url: str, token: str, method: str, params: dict | None = None,
         timeout: float = MCP_CALL_TIMEOUT):
    """发一条 JSON-RPC 请求，返回 result；任何失败都抛 McpError（消息可读）。"""
    payload = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        payload["params"] = params

    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        resp = requests.post(_endpoint(base_url), json=payload, headers=headers,
                             timeout=timeout)
    except requests.Timeout:
        raise McpError("连接超时")
    except Exception as e:
        raise McpError(f"无法连接服务器：{e}")

    if resp.status_code == 401:
        raise McpError("鉴权失败（401），请检查 auth_token")
    if resp.status_code != 200:
        raise McpError(f"服务器返回 HTTP {resp.status_code}")

    try:
        obj = resp.json()
    except ValueError:
        raise McpError("服务器返回的不是 JSON")
    if not isinstance(obj, dict):
        raise McpError("服务器返回的 JSON 结构不正确")

    error = obj.get("error")
    if isinstance(error, dict):
        code = error.get("code")
        msg = error.get("message") or "未知错误"
        raise McpError(f"JSON-RPC error {code}：{msg}")
    return obj.get("result")


def initialize(base_url: str, token: str, timeout: float = MCP_CONNECT_TIMEOUT) -> dict:
    result = _rpc(base_url, token, "initialize", {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": CLIENT_INFO,
    }, timeout=timeout)
    if not isinstance(result, dict):
        raise McpError("initialize 返回结构不正确")
    return result


def discover_tools(base_url: str, token: str, timeout: float = MCP_CONNECT_TIMEOUT) -> list[dict]:
    """先 initialize 握手，再 tools/list。返回 [{"name","description","inputSchema"}]。"""
    initialize(base_url, token, timeout=timeout)
    result = _rpc(base_url, token, "tools/list", {}, timeout=timeout)
    tools = (result or {}).get("tools") if isinstance(result, dict) else None
    if not isinstance(tools, list):
        raise McpError("tools/list 返回结构不正确")

    out = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        name = (t.get("name") or "").strip()
        # 技能引用只接受「小写字母开头、可含下划线、无连字符」的工具名，
        # 不合规的工具直接不进缓存（避免出现永远无法绑定的记录）。
        if not name or not TOOL_NAME_RE.match(name):
            continue
        out.append({
            "name": name,
            "description": t.get("description") or "",
            "inputSchema": t.get("inputSchema") or {},
        })
    return out


def extract_text(result) -> str:
    """MCP tools/call 的 content 是 [{"type":"text","text":...}] 列表，拼成纯文本。"""
    if isinstance(result, str):
        return result
    if not isinstance(result, dict):
        return ""
    pieces = []
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            pieces.append(item.get("text") or "")
        elif isinstance(item, str):
            pieces.append(item)
    return "\n".join(p for p in pieces if p).strip()


def call_tool(base_url: str, token: str, name: str, arguments: dict,
              timeout: float = MCP_CALL_TIMEOUT) -> str:
    """带令牌调远端 tools/call，返回文本 content；工具报错抛 McpError。"""
    result = _rpc(base_url, token, "tools/call",
                  {"name": name, "arguments": arguments or {}}, timeout=timeout)
    text = extract_text(result)
    if isinstance(result, dict) and result.get("isError"):
        raise McpError(text or f"工具 {name} 执行失败")
    if not text:
        raise McpError(f"工具 {name} 未返回文本内容")
    return text


# ---------------------------------------------------------------------------
# 序列化 —— auth_token 永不出现
# ---------------------------------------------------------------------------

def _server_json(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "base_url": row["base_url"],
        "connected": bool(row.get("connected")),
        "has_auth": bool(row.get("has_auth")),
        "tool_count": int(row.get("tool_count") or 0),
    }


def _tool_json(t: dict) -> dict:
    return {"name": t["name"], "description": t.get("description") or ""}


def _read_json() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _valid_base_url(url: str) -> bool:
    return bool(url) and bool(SCHEME_RE.match(url))


# ---------------------------------------------------------------------------
# 服务器登记
# ---------------------------------------------------------------------------

@doc(summary="列出本班 MCP 服务器", tags=["mcp"],
     responses={200: {"description": "本班服务器列表（不含 auth_token）",
                      "schema": {"type": "object", "properties": {
                          "servers": {"type": "array", "items": SERVER_SCHEMA}}}}})
@mcp_bp.route("/mcp/servers", methods=["GET"])
@require_auth
def list_servers():
    user = get_current_user()
    try:
        servers = db.list_mcp_servers(user["class_id"])
    except Exception:
        return _err("service unavailable", 503)
    return jsonify({"servers": [_server_json(s) for s in servers]})


@doc(summary="登记 MCP 服务器（教师）",
     description="保存 name / base_url / auth_token；auth_token 只存服务端，响应只给 has_auth。",
     tags=["mcp"],
     request_body={"content_type": "application/json", "schema": {
         "type": "object", "required": ["name", "base_url"],
         "properties": {"name": {"type": "string"}, "base_url": {"type": "string"},
                        "auth_token": {"type": "string"}}}},
     responses={201: {"description": "已登记（响应只含 has_auth，不含 auth_token）",
                      "schema": {"type": "object", "properties": {
                          "ok": {"type": "boolean"}, "id": {"type": "integer"},
                          "server": SERVER_SCHEMA}}},
                400: {"description": "参数错误/班级内重名", "schema": {"$ref": "#/components/schemas/Error"}},
                403: {"description": "仅教师", "schema": {"$ref": "#/components/schemas/Error"}}})
@mcp_bp.route("/mcp/servers", methods=["POST"])
@require_auth
@teacher_only
def create_server():
    user = get_current_user()
    data = _read_json()
    name = (data.get("name") or "").strip()
    base_url = (data.get("base_url") or "").strip()
    auth_token = data.get("auth_token")
    auth_token = auth_token.strip() if isinstance(auth_token, str) else ""

    if not name:
        return _err("name 必填", 400)
    if not SERVER_NAME_RE.match(name) or "@" in name:
        return _err("name 不能包含空白或 @", 400)
    if len(name) > 64:
        return _err("name 长度不能超过 64", 400)
    if not base_url:
        return _err("base_url 必填", 400)
    if not _valid_base_url(base_url):
        return _err("base_url 必须以 http:// 或 https:// 开头", 400)

    try:
        if db.get_mcp_server_by_name(name, user["class_id"]):
            return _err("名称已存在（班级内唯一）", 400)
        server_id = db.create_mcp_server(user["class_id"], name, base_url, auth_token)
    except Exception:
        return _err("service unavailable", 503)

    row = {"id": server_id, "name": name, "base_url": base_url,
           "connected": False, "has_auth": bool(auth_token), "tool_count": 0}
    return jsonify({"ok": True, "id": server_id, "server": _server_json(row)}), 201


@doc(summary="修改 MCP 服务器（教师）",
     description="改 name / base_url；auth_token 留空表示不改，填写则替换。地址或令牌变化会重置连接并清空工具缓存。跨班/不存在 404。",
     tags=["mcp"],
     request_body={"content_type": "application/json", "schema": {
         "type": "object", "properties": {"name": {"type": "string"},
                                          "base_url": {"type": "string"},
                                          "auth_token": {"type": "string",
                                                         "description": "留空表示不改"}}}},
     responses={200: {"description": "已修改",
                      "schema": {"type": "object", "properties": {
                          "ok": {"type": "boolean"}, "id": {"type": "integer"},
                          "server": SERVER_SCHEMA}}},
                400: {"description": "参数错误", "schema": {"$ref": "#/components/schemas/Error"}},
                403: {"description": "仅教师", "schema": {"$ref": "#/components/schemas/Error"}},
                404: {"description": "不存在/跨班", "schema": {"$ref": "#/components/schemas/Error"}}})
@mcp_bp.route("/mcp/servers/<int:server_id>", methods=["PUT"])
@require_auth
@teacher_only
def update_server(server_id: int):
    user = get_current_user()
    try:
        existing = db.get_mcp_server(server_id, user["class_id"])
    except Exception:
        return _err("service unavailable", 503)
    if existing is None:
        return _err("server not found", 404)

    data = _read_json()
    name = data.get("name", existing["name"])
    base_url = data.get("base_url", existing["base_url"])
    name = (name or "").strip()
    base_url = (base_url or "").strip()
    raw_token = data.get("auth_token")
    auth_token = raw_token.strip() if isinstance(raw_token, str) else ""

    if not name:
        return _err("name 不能为空", 400)
    if not SERVER_NAME_RE.match(name) or "@" in name:
        return _err("name 不能包含空白或 @", 400)
    if not _valid_base_url(base_url):
        return _err("base_url 必须以 http:// 或 https:// 开头", 400)

    try:
        dup = db.get_mcp_server_by_name(name, user["class_id"])
        if dup and dup["id"] != server_id:
            return _err("名称已存在（班级内唯一）", 400)
        db.update_mcp_server(server_id, name, base_url, auth_token or None)
        # 地址或令牌变了，旧的发现缓存不再可信：重置连接状态并清空工具缓存。
        if base_url != existing["base_url"] or auth_token:
            db.set_mcp_disconnected(server_id)
        servers = db.list_mcp_servers(user["class_id"])
    except Exception:
        return _err("service unavailable", 503)

    row = next((s for s in servers if s["id"] == server_id), None)
    return jsonify({"ok": True, "id": server_id,
                    "server": _server_json(row) if row else None})


@doc(summary="删除 MCP 服务器（教师）", description="级联清空工具缓存；跨班/不存在 404。",
     tags=["mcp"],
     responses={200: {"description": "已删除（工具缓存级联清空）",
                      "schema": {"type": "object", "properties": {
                          "ok": {"type": "boolean"}, "id": {"type": "integer"}}}},
                403: {"description": "仅教师", "schema": {"$ref": "#/components/schemas/Error"}},
                404: {"description": "不存在/跨班", "schema": {"$ref": "#/components/schemas/Error"}}})
@mcp_bp.route("/mcp/servers/<int:server_id>", methods=["DELETE"])
@require_auth
@teacher_only
def delete_server(server_id: int):
    user = get_current_user()
    try:
        existing = db.get_mcp_server(server_id, user["class_id"])
    except Exception:
        return _err("service unavailable", 503)
    if existing is None:
        return _err("server not found", 404)
    try:
        db.delete_mcp_server(server_id)
    except Exception:
        return _err("service unavailable", 503)
    return jsonify({"ok": True, "id": server_id})


# ---------------------------------------------------------------------------
# 连接 / 断开 / 工具缓存
# ---------------------------------------------------------------------------

@doc(summary="连接并发现工具（教师）",
     description="服务端带令牌向 base_url 发 initialize 握手 + tools/list，写工具缓存并置 connected=true。",
     tags=["mcp"],
     responses={200: {"description": "已连接并写入工具缓存",
                      "schema": {"type": "object", "properties": {
                          "connected": {"type": "boolean"},
                          "server_id": {"type": "integer"},
                          "server_name": {"type": "string"},
                          "tools": {"type": "array", "items": TOOL_SCHEMA}}}},
                400: {"description": "未配置 auth_token", "schema": {"$ref": "#/components/schemas/Error"}},
                403: {"description": "仅教师", "schema": {"$ref": "#/components/schemas/Error"}},
                404: {"description": "不存在/跨班", "schema": {"$ref": "#/components/schemas/Error"}},
                502: {"description": "连接失败（不可达/401/JSON-RPC error）",
                      "schema": {"$ref": "#/components/schemas/Error"}}})
@mcp_bp.route("/mcp/servers/<int:server_id>/connect", methods=["POST"])
@require_auth
@teacher_only
def connect_server(server_id: int):
    user = get_current_user()
    try:
        server = db.get_mcp_server(server_id, user["class_id"])
    except Exception:
        return _err("service unavailable", 503)
    if server is None:
        return _err("server not found", 404)

    token = server.get("auth_token") or ""
    if not token:
        return _err("该服务器未配置 auth_token，无法连接", 400)

    try:
        tools = discover_tools(server["base_url"], token)
    except McpError as e:
        return _err(f"连接失败：{e}", 502)

    try:
        db.save_mcp_discovery(server_id, tools)
    except Exception:
        return _err("service unavailable", 503)

    return jsonify({
        "connected": True,
        "server_id": server_id,
        "server_name": server["name"],
        "tools": [_tool_json(t) for t in tools],
    })


@doc(summary="断开 MCP 服务器（教师）", description="置 connected=false 并清空工具缓存，此后不可再调用。",
     tags=["mcp"],
     responses={200: {"description": "已断开并清空工具缓存",
                      "schema": {"type": "object", "properties": {
                          "connected": {"type": "boolean"},
                          "server_id": {"type": "integer"},
                          "tools": {"type": "array", "items": TOOL_SCHEMA}}}},
                403: {"description": "仅教师", "schema": {"$ref": "#/components/schemas/Error"}},
                404: {"description": "不存在/跨班", "schema": {"$ref": "#/components/schemas/Error"}}})
@mcp_bp.route("/mcp/servers/<int:server_id>/disconnect", methods=["POST"])
@require_auth
@teacher_only
def disconnect_server(server_id: int):
    user = get_current_user()
    try:
        server = db.get_mcp_server(server_id, user["class_id"])
    except Exception:
        return _err("service unavailable", 503)
    if server is None:
        return _err("server not found", 404)
    try:
        db.set_mcp_disconnected(server_id)
    except Exception:
        return _err("service unavailable", 503)
    return jsonify({"connected": False, "server_id": server_id, "tools": []})


@doc(summary="查看已发现的工具", tags=["mcp"],
     responses={200: {"description": "已发现的工具缓存",
                      "schema": {"type": "object", "properties": {
                          "server_id": {"type": "integer"},
                          "server_name": {"type": "string"},
                          "connected": {"type": "boolean"},
                          "tools": {"type": "array", "items": TOOL_SCHEMA}}}},
                404: {"description": "不存在/跨班", "schema": {"$ref": "#/components/schemas/Error"}}})
@mcp_bp.route("/mcp/servers/<int:server_id>/tools", methods=["GET"])
@require_auth
def list_server_tools(server_id: int):
    user = get_current_user()
    try:
        server = db.get_mcp_server(server_id, user["class_id"])
        if server is None:
            return _err("server not found", 404)
        tools = db.list_mcp_tools(server_id)
    except Exception:
        return _err("service unavailable", 503)

    return jsonify({
        "server_id": server_id,
        "server_name": server["name"],
        "connected": bool(server.get("connected")),
        "tools": [_tool_json(t) for t in tools],
    })


@doc(summary="MCP 工具调用审计（教师）",
     description="本班 mcp_tool_calls，按调用时间倒序；每次 tools/call（含 denied/error）一条。",
     tags=["mcp"],
     parameters=[{"name": "limit", "in": "query", "required": False,
                  "schema": {"type": "integer", "default": 50}, "description": "最多返回条数"}],
     responses={200: {"description": "本班审计记录",
                      "schema": {"type": "object", "properties": {
                          "calls": {"type": "array", "items": CALL_SCHEMA}}}},
                403: {"description": "仅教师", "schema": {"$ref": "#/components/schemas/Error"}}})
@mcp_bp.route("/mcp/tool-calls", methods=["GET"])
@require_auth
@teacher_only
def list_tool_calls():
    user = get_current_user()
    try:
        limit = int(request.args.get("limit", 50))
    except (TypeError, ValueError):
        limit = 50
    limit = max(1, min(limit, 200))
    try:
        rows = db.list_mcp_tool_calls(user["class_id"], limit=limit)
    except Exception:
        return _err("service unavailable", 503)
    return jsonify({"calls": rows})
