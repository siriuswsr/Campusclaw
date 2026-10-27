"""Lesson 6 — homework flow, part 1: publishing assignments + teacher grading.

POST /api/assignments            (teacher) publish to the teacher's own class
GET  /api/assignments            (any)     list the caller's class only
PUT  /api/submissions/<sid>/grade (teacher) override the AI grade

Class isolation: class_id is ALWAYS taken from the verified JWT. A class_id in the
body or query string is never trusted — asking for another class returns an empty
list (spec: 越权看到空数组，不暗示资料归属).
"""
from datetime import datetime, timezone
from functools import wraps

from flask import Blueprint, jsonify, request

from . import db
from .audit import record_audit
from .auth import get_current_user, require_auth

assignments_bp = Blueprint("assignments", __name__, url_prefix="/api")

REQUIRED_FIELDS = ("title", "body", "subject", "deadline")


def _err(message: str, status: int):
    """Homework-flow error body. Both keys carry the spec's message text so either
    `resp["error"]` or `resp["message"]` assertions match the规约 wording."""
    return jsonify({"error": message, "message": message}), status


def teacher_only(message: str, forbidden_action: str = None):
    """403 unless the token says role=teacher. Sits under @require_auth.

    Why not the shared role_required(): the spec pins a per-interface message
    («forbidden: 仅教师可发布作业» / «…仅教师可改分») and asks for an audit row on
    the publish attempt, none of which the generic decorator can express.
    """
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            user = get_current_user() or {}
            if user.get("role") != "teacher":
                if forbidden_action:
                    record_audit(forbidden_action, level="warning")
                return _err(message, 403)
            return view(*args, **kwargs)
        return wrapper
    return decorator


def _parse_deadline(value):
    """ISO 8601 → naive UTC datetime, because the column is DATETIME and every
    comparison in this module is against datetime.utcnow()."""
    deadline = datetime.fromisoformat(value)
    if deadline.tzinfo is not None:
        deadline = deadline.astimezone(timezone.utc).replace(tzinfo=None)
    return deadline


def _assignment_json(a: dict) -> dict:
    return {
        "id": a["id"],
        "title": a["title"],
        "body": a["body"],
        "subject": a["subject"],
        "deadline": a["deadline"].isoformat() if a["deadline"] else None,
        "status": a["status"],
        "class_id": a["class_id"],
    }


@assignments_bp.route("/assignments", methods=["POST"])
@require_auth
@teacher_only("forbidden: 仅教师可发布作业", "create_assignment_forbidden")
def create_assignment():
    data = request.get_json(silent=True) or {}
    for field in REQUIRED_FIELDS:
        if not str(data.get(field) or "").strip():
            return _err(f"{field} required", 400)

    try:
        deadline = _parse_deadline(data["deadline"])
    except (ValueError, TypeError):
        return _err("deadline must be ISO 8601", 400)

    user = get_current_user()
    try:
        assignment_id = db.create_assignment(
            class_id=user["class_id"],      # JWT only — body class_id is ignored
            teacher_id=user["id"],
            title=str(data["title"]).strip(),
            body=str(data["body"]),
            subject=str(data["subject"]).strip(),
            deadline=deadline,
        )
    except Exception:
        return _err("service unavailable", 503)

    record_audit("create_assignment", target_id=assignment_id)
    return jsonify({"assignment_id": assignment_id}), 201


@assignments_bp.route("/assignments", methods=["GET"])
@require_auth
def list_assignments():
    """教师看本班、学生看本班；显式点名他班直接给空数组。"""
    user = get_current_user()
    class_id = user["class_id"]

    requested = request.args.get("class_id", type=int)
    if requested and requested != class_id:
        return jsonify({"assignments": []})

    try:
        items = db.list_assignments(class_id)
    except Exception:
        return _err("service unavailable", 503)
    return jsonify({"assignments": [_assignment_json(a) for a in items]})


@assignments_bp.route("/submissions/<int:sid>/grade", methods=["PUT"])
@require_auth
@teacher_only("forbidden: 仅教师可改分")
def override_grade(sid: int):
    """教师改分覆盖 AI 终评；跨班与不存在同构 404。"""
    data = request.get_json(silent=True) or {}
    if data.get("final_score") is None or data.get("final_comment") is None:
        return _err("final_score and final_comment required", 400)

    user = get_current_user()
    try:
        sub = db.get_submission(sid)
    except Exception:
        return _err("service unavailable", 503)
    if not sub or sub["class_id"] != user["class_id"]:
        return _err("submission not found", 404)

    try:
        grade = db.get_grade(sid)
    except Exception:
        return _err("service unavailable", 503)
    if not grade:
        return _err("grading not done yet", 409)

    ai_score_before = grade.get("ai_score")
    try:
        final_score = int(data["final_score"])
    except (TypeError, ValueError):
        return _err("final_score must be an integer", 400)

    try:
        db.update_final_grade(sid, final_score, str(data["final_comment"]), user["id"])
    except Exception:
        return _err("service unavailable", 503)

    # 改分前后值都进审计（spec: 含改分前 ai_score 与改分后 final_score）
    record_audit("teacher_grade", target_id=sid,
                 ai_score=ai_score_before, final_score=final_score)
    return jsonify({"ok": True, "submission_id": sid,
                    "final_score": final_score, "graded_by": user["id"]})