"""Lesson 6 — homework flow, part 2: student submission + status polling.

POST /api/submissions        (student) submit an answer, then grade asynchronously
GET  /api/submissions/<sid>  (student: own only, teacher: own class only)

The POST MUST return immediately — 批改 runs in a background thread
(app/grader.py), so the client polls until status is done / failed.
"""
import json
from datetime import datetime

from flask import Blueprint, jsonify, request

from . import db
from .audit import record_audit
from .auth import get_current_user, require_auth

submissions_bp = Blueprint("submissions", __name__, url_prefix="/api")

DUPLICATE_ENTRY = 1062  # MySQL ER_DUP_ENTRY — the (assignment_id, student_id) UNIQUE


def _err(message: str, status: int):
    return jsonify({"error": message, "message": message}), status


@submissions_bp.route("/submissions", methods=["POST"])
@require_auth
def submit_homework():
    user = get_current_user()
    data = request.get_json(silent=True) or {}

    if data.get("assignment_id") is None or data.get("answer_text") is None:
        return _err("assignment_id and answer_text required", 400)
    answer_text = str(data["answer_text"])
    if not answer_text.strip():
        return _err("answer_text required", 400)

    try:
        assignment_id = int(data["assignment_id"])
    except (TypeError, ValueError):
        return _err("assignment_id must be an integer", 400)

    try:
        assignment = db.get_assignment(assignment_id, user["class_id"])
    except Exception:
        return _err("service unavailable", 503)
    # 他班作业与不存在同构：都是 404，不泄漏存在性
    if not assignment:
        return _err("assignment not found", 404)

    if assignment["deadline"] and datetime.utcnow() > assignment["deadline"]:
        return _err("deadline passed", 410)

    try:
        existing = db.find_submission(assignment_id, user["id"])
    except Exception:
        return _err("service unavailable", 503)
    if existing:
        return _err("already submitted", 409)

    try:
        submission_id = db.create_submission(
            assignment_id=assignment_id,
            student_id=user["id"],
            class_id=user["class_id"],
            answer_text=answer_text,
        )
    except Exception as e:
        # 并发双击：UNIQUE(assignment_id, student_id) 兜底成同一个 409
        if e.args and e.args[0] == DUPLICATE_ENTRY:
            return _err("already submitted", 409)
        return _err("service unavailable", 503)

    record_audit("submit_homework", target_id=submission_id, assignment_id=assignment_id)

    # 异步批改：起线程后立刻返回，不阻塞提交（spec: <200ms）
    from .grader import grade_async
    grade_async(submission_id, assignment_id)

    return jsonify({"submission_id": submission_id, "status": "pending"}), 201


@submissions_bp.route("/submissions", methods=["GET"])
@require_auth
def list_submissions():
    """提交列表：学生只看本人，教师只看本班（class_id / student_id 取自 JWT）。"""
    user = get_current_user()
    try:
        if user["role"] == "teacher":
            rows = db.list_submissions_for_class(user["class_id"])
        else:
            rows = db.list_submissions_for_student(user["id"])
    except Exception:
        return _err("service unavailable", 503)
    return jsonify({"submissions": [_submission_list_json(r) for r in rows]})


def _submission_list_json(row: dict) -> dict:
    """提交列表单项（含 LEFT JOIN 带回的作业信息与批改 / 终评）。"""
    grade = None
    if row.get("ai_score") is not None or row.get("final_score") is not None:
        citations = row.get("ai_citations")
        if isinstance(citations, (str, bytes)):
            try:
                citations = json.loads(citations)
            except Exception:
                citations = []
        grade = {
            "ai_score": row.get("ai_score"),
            "ai_comment": row.get("ai_comment"),
            "ai_citations": citations,
            "final_score": row.get("final_score"),
            "final_comment": row.get("final_comment"),
        }
    return {
        "id": row["id"],
        "assignment_id": row["assignment_id"],
        "assignment_title": row["assignment_title"],
        "subject": row["subject"],
        "status": row["status"],
        "answer_text": row["answer_text"],
        "error_msg": row["error_msg"],
        "student_name": row["student_name"],
        "submitted_at": row["submitted_at"].isoformat() if row.get("submitted_at") else None,
        "grade": grade,
    }


@submissions_bp.route("/submissions/<int:sid>", methods=["GET"])
@require_auth
def get_submission(sid: int):
    """轮询入口。学生只能看本人、教师只能看本班，越权与不存在同构 404。"""
    user = get_current_user()
    try:
        sub = db.get_submission(sid)
    except Exception:
        return _err("service unavailable", 503)
    if not sub:
        return _err("not found", 404)

    if user["role"] == "student" and sub["student_id"] != user["id"]:
        return _err("not found", 404)
    if user["role"] == "teacher" and sub["class_id"] != user["class_id"]:
        return _err("not found", 404)

    try:
        grade = db.get_grade(sid)
    except Exception:
        return _err("service unavailable", 503)

    return jsonify({
        "id": sub["id"],
        "assignment_id": sub["assignment_id"],
        "status": sub["status"],
        "answer_text": sub["answer_text"],
        "error_msg": sub["error_msg"],
        "grade": {
            "ai_score": grade.get("ai_score"),
            "ai_comment": grade.get("ai_comment"),
            "ai_citations": grade.get("ai_citations"),
            "final_score": grade.get("final_score"),
            "final_comment": grade.get("final_comment"),
        } if grade else None,
    })