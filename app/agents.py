"""辅导智能体管理接口（提示词 C）。

    GET    /api/agents           列出本班智能体；学生只看到 enabled=true 的
    POST   /api/agents           教师新建（含 skill_ids / material_ids 绑定）
    PUT    /api/agents/<id>      教师修改（覆盖更新 + 重建绑定）
    DELETE /api/agents/<id>      教师删除（级联清两张绑定表）

所有写接口非教师一律 403；跨班访问与不存在同构 404。
绑定的 skill_id / material_id 必须是本班资源，否则 400 —— 班级是数据边界。
"""
from functools import wraps

from flask import Blueprint, jsonify, request

from . import db
from .auth import get_current_user, require_auth
from .openapi import doc

agents_bp = Blueprint("agents", __name__, url_prefix="/api")

AGENT_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "integer"},
        "name": {"type": "string"},
        "subject": {"type": "string", "nullable": True},
        "prompt": {"type": "string"},
        "enabled": {"type": "boolean"},
        "skill_ids": {"type": "array", "items": {"type": "integer"}},
        "material_ids": {"type": "array", "items": {"type": "integer"}},
    },
}


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


def _read_ids(value):
    """把 body 里的 skill_ids/material_ids 规整成去重后的 int 列表。"""
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        return []
    out = []
    for item in value:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return sorted(set(out))


@doc(summary="列出本班智能体", description="学生只看到 enabled=true 的智能体；教师看到全部。",
     tags=["agents"],
     responses={
         200: {"description": "OK", "schema": {"type": "object", "properties": {
             "agents": {"type": "array", "items": AGENT_SCHEMA}}}},
         401: {"description": "未登录", "schema": {"$ref": "#/components/schemas/Error"}},
         503: {"description": "服务不可用", "schema": {"$ref": "#/components/schemas/Error"}},
     })
@agents_bp.route("/agents", methods=["GET"])
@require_auth
def list_agents():
    user = get_current_user()
    enabled_only = user["role"] != "teacher"
    try:
        agents = db.list_agents(user["class_id"], enabled_only=enabled_only)
    except Exception:
        return _err("service unavailable", 503)
    return jsonify({"agents": [_agent_json(a) for a in agents]})


@doc(summary="新建智能体（教师）", description="同时提交 skill_ids 与 material_ids，写入主表与两张绑定表；非教师 403，同名/跨班资源 400。",
     tags=["agents"],
     request_body={"schema": {
         "type": "object",
         "required": ["name"],
         "properties": {
             "name": {"type": "string"},
             "prompt": {"type": "string", "maxLength": 2000},
             "subject": {"type": "string"},
             "enabled": {"type": "boolean"},
             "skill_ids": {"type": "array", "items": {"type": "integer"}},
             "material_ids": {"type": "array", "items": {"type": "integer"}},
         }}},
     responses={
         201: {"description": "已创建", "schema": {"type": "object", "properties": {
             "ok": {"type": "boolean"}, "id": {"type": "integer"}}}},
         400: {"description": "参数错误", "schema": {"$ref": "#/components/schemas/Error"}},
         403: {"description": "仅教师", "schema": {"$ref": "#/components/schemas/Error"}},
     })
@agents_bp.route("/agents", methods=["POST"])
@require_auth
@teacher_only
def create_agent():
    user = get_current_user()
    data = request.get_json(silent=True) or {}

    name = (data.get("name") or "").strip()
    if not name:
        return _err("名称必填", 400)
    prompt = data.get("prompt") or ""
    if len(prompt) > 2000:
        return _err("角色提示词超过 2000 字上限", 400)
    enabled = bool(data.get("enabled", True))
    subject = (data.get("subject") or "").strip() or None
    skill_ids = _read_ids(data.get("skill_ids"))
    material_ids = _read_ids(data.get("material_ids"))

    try:
        if db.get_agent_by_name(name, user["class_id"]):
            return _err("名称已存在（班级内唯一）", 400)
        valid_skills = db.list_skill_ids_for_class(user["class_id"])
        valid_materials = db.list_material_ids_for_class(user["class_id"])
        if not set(skill_ids) <= valid_skills or not set(material_ids) <= valid_materials:
            return _err("存在本班之外的技能或材料", 400)
        agent_id = db.create_agent(
            class_id=user["class_id"], name=name, prompt=prompt, enabled=enabled,
            subject=subject, skill_ids=skill_ids, material_ids=material_ids,
        )
    except Exception:
        return _err("service unavailable", 503)

    return jsonify({"ok": True, "id": agent_id}), 201


@doc(summary="修改智能体（教师）", description="覆盖更新，重建两张绑定表；跨班/不存在 404（与不存在同构）。",
     tags=["agents"],
     request_body={"schema": {
         "type": "object",
         "properties": {
             "name": {"type": "string"},
             "prompt": {"type": "string", "maxLength": 2000},
             "subject": {"type": "string"},
             "enabled": {"type": "boolean"},
             "skill_ids": {"type": "array", "items": {"type": "integer"}},
             "material_ids": {"type": "array", "items": {"type": "integer"}},
         }}},
     responses={
         200: {"description": "OK", "schema": {"type": "object", "properties": {
             "ok": {"type": "boolean"}, "id": {"type": "integer"}}}},
         403: {"description": "仅教师", "schema": {"$ref": "#/components/schemas/Error"}},
         404: {"description": "不存在/跨班", "schema": {"$ref": "#/components/schemas/Error"}},
     })
@agents_bp.route("/agents/<int:agent_id>", methods=["PUT"])
@require_auth
@teacher_only
def update_agent(agent_id: int):
    user = get_current_user()
    data = request.get_json(silent=True) or {}

    try:
        existing = db.get_agent(agent_id, user["class_id"])
    except Exception:
        return _err("service unavailable", 503)
    if existing is None:
        return _err("agent not found", 404)

    name = (data.get("name") or "").strip()
    if not name:
        return _err("名称必填", 400)
    prompt = data.get("prompt") or ""
    if len(prompt) > 2000:
        return _err("角色提示词超过 2000 字上限", 400)
    enabled = bool(data.get("enabled", True))
    subject = (data.get("subject") or "").strip() or None
    skill_ids = _read_ids(data.get("skill_ids"))
    material_ids = _read_ids(data.get("material_ids"))

    try:
        dup = db.get_agent_by_name(name, user["class_id"])
        if dup and dup["id"] != agent_id:
            return _err("名称已存在（班级内唯一）", 400)
        valid_skills = db.list_skill_ids_for_class(user["class_id"])
        valid_materials = db.list_material_ids_for_class(user["class_id"])
        if not set(skill_ids) <= valid_skills or not set(material_ids) <= valid_materials:
            return _err("存在本班之外的技能或材料", 400)
        db.update_agent(
            agent_id=agent_id, name=name, prompt=prompt, enabled=enabled,
            subject=subject, skill_ids=skill_ids, material_ids=material_ids,
        )
    except Exception:
        return _err("service unavailable", 503)

    return jsonify({"ok": True, "id": agent_id})


@doc(summary="删除智能体（教师）", description="级联清空 tutor_agent_skills / tutor_agent_materials；跨班/不存在 404。",
     tags=["agents"],
     responses={
         200: {"description": "OK", "schema": {"type": "object", "properties": {
             "ok": {"type": "boolean"}, "id": {"type": "integer"}}}},
         403: {"description": "仅教师", "schema": {"$ref": "#/components/schemas/Error"}},
         404: {"description": "不存在/跨班", "schema": {"$ref": "#/components/schemas/Error"}},
     })
@agents_bp.route("/agents/<int:agent_id>", methods=["DELETE"])
@require_auth
@teacher_only
def delete_agent(agent_id: int):
    user = get_current_user()
    try:
        existing = db.get_agent(agent_id, user["class_id"])
    except Exception:
        return _err("service unavailable", 503)
    if existing is None:
        return _err("agent not found", 404)
    try:
        db.delete_agent(agent_id)
    except Exception:
        return _err("service unavailable", 503)
    return jsonify({"ok": True, "id": agent_id})