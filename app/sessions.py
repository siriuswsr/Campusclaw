"""会话（solve_sessions / solve_messages）接口 —— 提示词 F。

    GET    /api/sessions                本人会话列表（按最后消息时间倒序）
    POST   /api/sessions                {agent_id} 新建；agent 不存在/停用/跨班 → 404
    GET    /api/sessions/<id>           本人会话详情（最近 6 条 + 智能体快照）
    POST   /api/sessions/<id>/messages  {question} → SSE 流（四类事件，同 ask）

边界规则：
  * 会话归 user 所有（student_id = JWT 的 user id）；他人/他班/不存在的会话一律
    404，响应体与「不存在」完全一致（防跨班与跨用户探测）。
  * agent 的 enabled 状态在每次提问时重读，不冻结在创建时刻的快照 —— 老师停用
    智能体后，已存在的会话仍能继续提问（提示词 F 第 5 步）。
"""
from flask import Blueprint, jsonify, request

from . import db, llm_traffic
from .assistant import build_stream, _sse_response
from .auth import get_current_user, require_auth
from .openapi import doc

sessions_bp = Blueprint("sessions", __name__, url_prefix="/api")


def _err(message: str, status: int):
    return jsonify({"error": message, "message": message}), status


def _session_json(s: dict) -> dict:
    return {
        "id": s["id"],
        "agent_id": s.get("agent_id"),
        "agent_name": s.get("agent_name"),
        "status": s.get("status"),
        "message_count": s.get("message_count", 0),
        "created_at": s["created_at"].isoformat() if s.get("created_at") else None,
        "updated_at": s["updated_at"].isoformat() if s.get("updated_at") else None,
    }


def _agent_json(a: dict) -> dict:
    return {
        "id": a["id"],
        "name": a["name"],
        "subject": a.get("subject"),
        "prompt": a.get("prompt") or "",
        "enabled": bool(a["enabled"]),
        "skill_ids": a.get("skill_ids", []),
        "material_ids": a.get("material_ids", []),
    }


@doc(summary="会话列表", description="只返回本人的会话，按最后消息时间倒序。",
     tags=["sessions"],
     responses={200: {"description": "OK", "schema": {"type": "object", "properties": {
         "sessions": {"type": "array", "items": {"type": "object", "properties": {
             "id": {"type": "integer"}, "agent_id": {"type": "integer"},
             "agent_name": {"type": "string"}, "status": {"type": "string"},
             "message_count": {"type": "integer"}}}}}}}})
@sessions_bp.route("/sessions", methods=["GET"])
@require_auth
def list_sessions():
    user = get_current_user()
    try:
        rows = db.list_sessions(user["id"])
    except Exception:
        return _err("service unavailable", 503)
    return jsonify({"sessions": [_session_json(s) for s in rows]})


@doc(summary="新建会话", description="绑定当前 tab 的智能体；agent 不存在/已停用/跨班 → 404（与不存在同构）。",
     tags=["sessions"],
     request_body={"schema": {"type": "object", "required": ["agent_id"],
                              "properties": {"agent_id": {"type": "integer"}}}},
     responses={
         201: {"description": "已创建", "schema": {"type": "object", "properties": {
             "ok": {"type": "boolean"}, "id": {"type": "integer"},
             "agent_id": {"type": "integer"}}}},
         404: {"description": "智能体不存在/停用/跨班", "schema": {"$ref": "#/components/schemas/Error"}},
     })
@sessions_bp.route("/sessions", methods=["POST"])
@require_auth
def create_session():
    """新建会话：班级内启用中的智能体才行；不存在/停用/跨班 → 404（同构）。"""
    user = get_current_user()
    data = request.get_json(silent=True) or {}
    agent_id = data.get("agent_id")
    if agent_id is None:
        return _err("agent_id required", 400)
    try:
        agent_id = int(agent_id)
    except (TypeError, ValueError):
        return _err("agent not found", 404)

    try:
        agent = db.get_agent(agent_id, user["class_id"])
    except Exception:
        return _err("service unavailable", 503)
    if agent is None or not agent.get("enabled"):
        return _err("agent not found", 404)

    try:
        session_id = db.create_session(agent_id, user["id"], user["class_id"])
    except Exception:
        return _err("service unavailable", 503)

    return jsonify({"ok": True, "id": session_id, "agent_id": agent_id}), 201


@doc(summary="会话详情", description="最近 6 条消息 + 当前智能体快照（prompt / 技能 / 材料绑定）；他人/他班/不存在 → 404。",
     tags=["sessions"],
     responses={
         200: {"description": "OK", "schema": {"type": "object", "properties": {
             "session": {"type": "object"}, "agent": {"type": "object"},
             "messages": {"type": "array", "items": {"type": "object", "properties": {
                 "role": {"type": "string"}, "content": {"type": "string"}}}}}}},
         404: {"description": "不存在/跨用户", "schema": {"$ref": "#/components/schemas/Error"}},
     })
@sessions_bp.route("/sessions/<int:session_id>", methods=["GET"])
@require_auth
def get_session(session_id: int):
    """本人会话详情：最近 6 条消息 + 当前智能体快照（prompt / 技能 / 材料绑定）。"""
    user = get_current_user()
    try:
        session = db.get_session(session_id, user["id"])
    except Exception:
        return _err("service unavailable", 503)
    if session is None:
        return _err("session not found", 404)

    agent = None
    if session.get("agent_id"):
        try:
            agent = db.get_agent(session["agent_id"], user["class_id"])
        except Exception:
            agent = None

    try:
        messages = db.list_session_messages(session_id, limit=6)
    except Exception:
        return _err("service unavailable", 503)

    return jsonify({
        "session": {
            "id": session["id"],
            "agent_id": session.get("agent_id"),
            "status": session.get("status"),
        },
        "agent": _agent_json(agent) if agent else None,
        "messages": [
            {"role": m["role"], "content": m["content"]} for m in messages
        ],
    })


@doc(summary="会话提问（SSE 流）", description="读最近 6 条历史后作答；四类事件 meta/delta/done/error，异常时 HTTP 仍为 200。",
     tags=["sessions"],
     request_body={"schema": {"type": "object", "required": ["question"],
                              "properties": {"question": {"type": "string"}}}},
     responses={
         200: {"description": "text/event-stream", "schema": {"type": "string"}},
         400: {"description": "缺少 question", "schema": {"$ref": "#/components/schemas/Error"}},
         404: {"description": "会话不存在/跨用户", "schema": {"$ref": "#/components/schemas/Error"}},
     })
@sessions_bp.route("/sessions/<int:session_id>/messages", methods=["POST"])
@require_auth
def post_message(session_id: int):
    """在会话里提问 → SSE 流。智能体每次重新读取（enabled 不在本步校验）。"""
    user = get_current_user()
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return _err("question required", 400)

    try:
        session = db.get_session(session_id, user["id"])
    except Exception:
        return _err("service unavailable", 503)
    if session is None or not session.get("agent_id"):
        return _err("session not found", 404)

    try:
        agent = db.get_agent(session["agent_id"], user["class_id"])
    except Exception:
        return _err("service unavailable", 503)
    if agent is None:
        return _err("session not found", 404)

    try:
        agent["skills"] = db.list_agent_skills(agent["id"])
    except Exception:
        return _err("service unavailable", 503)

    # 生成器在请求上下文之外迭代，scene 必须在视图里先读出来再传下去。
    scene = llm_traffic.current_scene()
    return _sse_response(build_stream(user["class_id"], agent, question, session["id"],
                                      user_id=user["id"], scene=scene))
