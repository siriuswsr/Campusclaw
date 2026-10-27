"""GET /api/lectures — the 讲义 list shown on the teacher assistant-config screen
(知识库绑定 panel).

Class boundary holds here as everywhere else: the class comes from the token, so
?subject=… can never widen the result set. The query string is accepted and
ignored — lecture_notes has no subject column.
"""
from flask import Blueprint, jsonify

from . import db
from .auth import get_current_user, require_auth

lectures_bp = Blueprint("lectures", __name__, url_prefix="/api")


@lectures_bp.route("/lectures", methods=["GET"])
@require_auth
def list_lectures():
    user = get_current_user() or {}
    try:
        notes = db.list_lecture_notes(user["class_id"])
    except Exception:
        return jsonify({"error": "service unavailable"}), 503
    return jsonify({
        "lectures": [
            {"id": n["id"], "title": n["title"], "class_id": n["class_id"]}
            for n in notes
        ]
    })