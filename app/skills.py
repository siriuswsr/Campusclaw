"""技能文件管理接口（提示词 C）。

    POST   /api/skills          教师上传（text/plain 全文）→ parse_skill 校验后写入
    GET    /api/skills          列出本班技能（含 binding_count，删除二次确认用）
    PUT    /api/skills/<id>     教师替换（text/plain 全文）
    DELETE /api/skills/<id>     教师删除（级联清 tutor_agent_skills 绑定）

班内名称唯一（DB UNIQUE(class_id, name)），重复提交返回 400。所有写接口
非教师一律 403；跨班访问与不存在同构 404。
"""
from functools import wraps

from flask import Blueprint, jsonify, request

from . import db
from .auth import get_current_user, require_auth
from .openapi import doc
from .skills_parser import mcp_tool_refs, parse_skill

skills_bp = Blueprint("skills", __name__, url_prefix="/api")

SKILL_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "integer"},
        "name": {"type": "string"},
        "trigger_text": {"type": "string"},
        "subject_text": {"type": "string", "nullable": True},
        "enabled": {"type": "boolean"},
        "description": {"type": "string"},
        "source_text": {"type": "string"},
        "binding_count": {"type": "integer"},
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


def _read_skill_text():
    """text/plain 全文；也容忍 form/空白，返回去首尾空格后的原文或 None。"""
    raw = request.get_data(as_text=True)
    if raw is None or raw == "":
        return None
    return raw


def _validate_mcp_refs(tools_json: str, class_id: int):
    """保存技能前校验 MCP 引用：服务器属于本班、已连接、工具已在发现缓存中。

    返回可读错误消息；全部通过返回 None。DB 异常向上抛，由调用方转 503。
    """
    for tool_name, server_name in mcp_tool_refs(tools_json):
        server = db.get_mcp_server_by_name(server_name, class_id)
        if server is None:
            return f"工具引用 {tool_name}@{server_name}：服务器「{server_name}」不存在或不属于本班"
        if not server.get("connected"):
            return f"工具引用 {tool_name}@{server_name}：服务器「{server_name}」未连接，请先连接后再保存"
        if db.get_mcp_tool(server["id"], tool_name) is None:
            return (f"工具引用 {tool_name}@{server_name}："
                    f"服务器「{server_name}」的已发现工具中没有「{tool_name}」")
    return None


def _skill_json(s: dict) -> dict:
    return {
        "id": s["id"],
        "name": s["name"],
        "trigger_text": s["trigger_text"],
        "subject_text": s["subject_text"],
        "tools_json": s["tools_json"],
        "enabled": s["enabled"],
        "description": s["description"],
        "source_text": s.get("source_text"),
        "seed_key": s.get("seed_key"),
        "binding_count": s.get("binding_count", 0),
    }


@doc(summary="列出本班技能", description="含每个技能被多少智能体绑定（binding_count）。",
     tags=["skills"],
     responses={200: {"description": "OK", "schema": {"type": "object", "properties": {
         "skills": {"type": "array", "items": SKILL_SCHEMA}}}}})
@skills_bp.route("/skills", methods=["GET"])
@require_auth
def list_skills():
    user = get_current_user()
    try:
        skills = db.list_skills(user["class_id"])
    except Exception:
        return _err("service unavailable", 503)
    return jsonify({"skills": [_skill_json(s) for s in skills]})


@doc(summary="上传技能文件（教师）", description="请求体为 text/plain 技能文件全文；parse_skill 校验，缺字段/重复字段/非法工具名 → 400。",
     tags=["skills"],
     request_body={"content_type": "text/plain; charset=utf-8",
                   "schema": {"type": "string",
                              "description": "元数据区（名称/适用条件/资料/工具/启用）+ 空行 + 正文区"}},
     responses={
         201: {"description": "已创建", "schema": {"type": "object", "properties": {
             "ok": {"type": "boolean"}, "id": {"type": "integer"}, "skill": SKILL_SCHEMA}}},
         400: {"description": "校验失败", "schema": {"$ref": "#/components/schemas/Error"}},
         403: {"description": "仅教师", "schema": {"$ref": "#/components/schemas/Error"}},
     })
@skills_bp.route("/skills", methods=["POST"])
@require_auth
@teacher_only
def create_skill():
    user = get_current_user()
    source = _read_skill_text()
    if source is None:
        return _err("请提供技能文件全文", 400)

    try:
        parsed = parse_skill(source)
    except ValueError as e:
        return _err(str(e), 400)

    try:
        ref_error = _validate_mcp_refs(parsed["tools_json"], user["class_id"])
    except Exception:
        return _err("service unavailable", 503)
    if ref_error:
        return _err(ref_error, 400)

    try:
        if db.get_skill_by_name(parsed["name"], user["class_id"]):
            return _err("名称已存在（班级内唯一）", 400)
        skill_id = db.create_skill(
            class_id=user["class_id"],
            name=parsed["name"],
            trigger_text=parsed["trigger_text"],
            subject_text=parsed["subject_text"],
            tools_json=parsed["tools_json"],
            enabled=parsed["enabled"],
            description=parsed["description"],
            source_text=parsed["source_text"],
        )
    except Exception:
        return _err("service unavailable", 503)

    return jsonify({"ok": True, "id": skill_id, "skill": {
        "id": skill_id,
        "name": parsed["name"],
        "trigger_text": parsed["trigger_text"],
        "subject_text": parsed["subject_text"],
        "enabled": parsed["enabled"],
    }}), 201


@doc(summary="替换技能文件（教师）", description="请求体同 POST：text/plain 全文替换；跨班/不存在 404。",
     tags=["skills"],
     request_body={"content_type": "text/plain; charset=utf-8", "schema": {"type": "string"}},
     responses={
         200: {"description": "OK", "schema": {"type": "object", "properties": {
             "ok": {"type": "boolean"}, "id": {"type": "integer"}}}},
         400: {"description": "校验失败", "schema": {"$ref": "#/components/schemas/Error"}},
         403: {"description": "仅教师", "schema": {"$ref": "#/components/schemas/Error"}},
         404: {"description": "不存在/跨班", "schema": {"$ref": "#/components/schemas/Error"}},
     })
@skills_bp.route("/skills/<int:skill_id>", methods=["PUT"])
@require_auth
@teacher_only
def update_skill(skill_id: int):
    user = get_current_user()
    source = _read_skill_text()
    if source is None:
        return _err("请提供技能文件全文", 400)

    try:
        existing = db.get_skill(skill_id, user["class_id"])
    except Exception:
        return _err("service unavailable", 503)
    if existing is None:
        return _err("skill not found", 404)

    try:
        parsed = parse_skill(source)
    except ValueError as e:
        return _err(str(e), 400)

    try:
        ref_error = _validate_mcp_refs(parsed["tools_json"], user["class_id"])
    except Exception:
        return _err("service unavailable", 503)
    if ref_error:
        return _err(ref_error, 400)

    try:
        dup = db.get_skill_by_name(parsed["name"], user["class_id"])
        if dup and dup["id"] != skill_id:
            return _err("名称已存在（班级内唯一）", 400)
        db.update_skill(
            skill_id=skill_id,
            name=parsed["name"],
            trigger_text=parsed["trigger_text"],
            subject_text=parsed["subject_text"],
            tools_json=parsed["tools_json"],
            enabled=parsed["enabled"],
            description=parsed["description"],
            source_text=parsed["source_text"],
        )
    except Exception:
        return _err("service unavailable", 503)

    return jsonify({"ok": True, "id": skill_id})


@doc(summary="删除技能（教师）", description="级联清空 tutor_agent_skills 中的绑定；跨班/不存在 404。",
     tags=["skills"],
     responses={
         200: {"description": "OK", "schema": {"type": "object", "properties": {
             "ok": {"type": "boolean"}, "id": {"type": "integer"}}}},
         403: {"description": "仅教师", "schema": {"$ref": "#/components/schemas/Error"}},
         404: {"description": "不存在/跨班", "schema": {"$ref": "#/components/schemas/Error"}},
     })
@skills_bp.route("/skills/<int:skill_id>", methods=["DELETE"])
@require_auth
@teacher_only
def delete_skill(skill_id: int):
    user = get_current_user()
    try:
        existing = db.get_skill(skill_id, user["class_id"])
    except Exception:
        return _err("service unavailable", 503)
    if existing is None:
        return _err("skill not found", 404)
    try:
        db.delete_skill(skill_id)
    except Exception:
        return _err("service unavailable", 503)
    return jsonify({"ok": True, "id": skill_id})