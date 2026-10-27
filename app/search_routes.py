"""Search blueprint — GET /api/search. All class_id filtering is enforced in
retrieval.py via the class_id taken from the verified JWT.

REQUEST BODY / QUERY / HEADER class_id MUST BE IGNORED (spec line 77-82).
Cross-class requests render as HTTP 200 empty hits (spec line 90-92).
"""
from flask import Blueprint, request, jsonify
from .auth import require_auth, get_current_user
from . import retrieval
from .openapi import doc
from .retrieval import QdrantUnavailable

search_bp = Blueprint("search", __name__, url_prefix="/api")


@doc(summary="检索", description="班级边界由 token 决定；mode=keyword|vector|hybrid，默认 hybrid。Qdrant 不可用时 vector/hybrid 返回 503，不编造分数。",
     tags=["search"],
     parameters=[
         {"name": "q", "in": "query", "required": True,
          "schema": {"type": "string"}, "description": "查询串（空 → 400）"},
         {"name": "mode", "in": "query", "required": False,
          "schema": {"type": "string", "enum": ["keyword", "vector", "hybrid"]},
          "description": "检索模式，默认 hybrid"},
         {"name": "top_k", "in": "query", "required": False,
          "schema": {"type": "integer", "default": 20}, "description": "返回条数（1-200）"},
     ],
     responses={
         200: {"description": "OK", "schema": {"type": "object", "properties": {
             "mode": {"type": "string"},
             "hits": {"type": "array", "items": {"type": "object", "properties": {
                 "chunk_id": {"type": "integer"}, "chunk_index": {"type": "integer"},
                 "material_id": {"type": "integer"}, "material_title": {"type": "string"},
                 "char_start": {"type": "integer"}, "char_end": {"type": "integer"},
                 "excerpt": {"type": "string"}, "score": {"type": "number"}}}}}}},
         400: {"description": "缺少 q", "schema": {"$ref": "#/components/schemas/Error"}},
         503: {"description": "向量服务不可用", "schema": {"$ref": "#/components/schemas/Error"}},
     })
@search_bp.route("/search", methods=["GET"])
@require_auth
def search_api():
    """GET /api/search?mode=keyword|vector|hybrid&q=...&top_k=..."""
    user = get_current_user()
    class_id = user["class_id"]   # ONLY source of truth; client params below discarded

    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"error": "query required"}), 400

    mode = (request.args.get("mode") or "hybrid").strip().lower()
    if mode not in ("keyword", "vector", "hybrid"):
        return jsonify({"error": f"invalid mode: {mode}"}), 400

    try:
        top_k = int(request.args.get("top_k", "20"))
        top_k = max(1, min(200, top_k))
    except ValueError:
        top_k = 20

    # subject 是「班级内」的收窄条件（非安全字段）：只保留 materials.subject 命中者。
    subject = (request.args.get("subject") or "").strip() or None

    # IMPORTANT: request.args.get("class_id") / JSON / header class_id — ALL IGNORED.
    # retrieval.search() receives ONLY the class_id from the verified token.

    try:
        result = retrieval.search(mode, q, class_id, top_k, subject_keyword=subject)
    except QdrantUnavailable:
        # spec line 142: vector AND hybrid both 503 when Qdrant is down.
        # NO partial results, NO fabricated scores.
        return jsonify({"error": "vector service unavailable"}), 503
    except Exception as e:
        return jsonify({"error": "service unavailable", "detail": str(e)}), 503

    return jsonify(result.to_json()), 200
