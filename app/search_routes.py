"""Search blueprint — GET /api/search. All class_id filtering is enforced in
retrieval.py via session-supplied class_id.

REQUEST BODY / QUERY / HEADER class_id MUST be IGNORED (spec line 77-82).
Cross-class requests render as HTTP 200 empty hits (spec line 90-92).
"""
from flask import Blueprint, request, jsonify
from .auth import login_required, get_current_user
from . import retrieval
from .retrieval import QdrantUnavailable

search_bp = Blueprint("search", __name__, url_prefix="/api")


@search_bp.route("/search", methods=["GET"])
@login_required
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

    # IMPORTANT: request.args.get("class_id") / JSON / header class_id — ALL IGNORED.
    # retrieval.search() receives ONLY session-supplied class_id.

    try:
        result = retrieval.search(mode, q, class_id, top_k)
    except QdrantUnavailable:
        # spec line 142: vector AND hybrid both 503 when Qdrant is down.
        # NO partial results, NO fabricated scores.
        return jsonify({"error": "vector service unavailable"}), 503
    except Exception as e:
        return jsonify({"error": "service unavailable", "detail": str(e)}), 503

    return jsonify(result.to_json()), 200
