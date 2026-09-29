"""Materials routes — list, get by id, upload (teacher only), download.
ALL data reads force class_id from session — never trust client input.
Post-upload indexing (chunks + embeddings + Qdrant) is in app.indexing.
"""
import os
import uuid
from flask import Blueprint, request, session, jsonify, send_file, render_template

from . import db
from .auth import login_required, role_required, get_current_user
from .knowledge import parse_file, ALLOWED_EXTENSIONS
from .config import Config
from .indexing import post_upload as _post_upload_indexing, reindex as _reindex_indexing

materials_bp = Blueprint("materials", __name__, url_prefix="/materials")


def _db_error():
    return jsonify({"error": "service unavailable"}), 503


def _safe_filename(filename: str) -> str:
    name = os.path.basename(filename)
    name = name.replace("/", "_").replace("\\", "_").replace("\x00", "")
    return name or "unnamed"


# ---------------------------------------------------------------------------
# HTML pages
# ---------------------------------------------------------------------------

@materials_bp.route("", methods=["GET"])
@login_required
def list_page():
    user = get_current_user()
    try:
        items = db.list_materials(user["class_id"])
    except Exception:
        return _db_error()
    if request.headers.get("Accept") == "application/json":
        return jsonify(items)
    return render_template("materials.html", items=items, user=user)


@materials_bp.route("/<int:mid>", methods=["GET"])
@login_required
def detail_page(mid: int):
    """Material detail page (查看/下载入口).

    Cross-class and nonexistent render the SAME template with an identical
    friendly 404 — no ownership hint. Body/ownership filtering is done by
    db.get_material(mid, session class_id); the page performs no judgement.
    """
    user = get_current_user()
    try:
        item = db.get_material(mid, user["class_id"])
        entry = db.get_knowledge_entry(mid, user["class_id"]) if item else None
    except Exception:
        return _db_error()
    if item is None:
        return render_template("material_detail.html", user=user, item=None), 404
    return render_template("material_detail.html", user=user, item=item,
                           body=(entry or {}).get("body_text", ""))


# ---------------------------------------------------------------------------
# API routes (JSON)
# ---------------------------------------------------------------------------

@materials_bp.route("/api", methods=["GET"])
@login_required
def list_api():
    """JSON list — class_id forced from session, client params ignored."""
    user = get_current_user()
    try:
        items = db.list_materials(user["class_id"])
    except Exception:
        return _db_error()
    return jsonify(items)


@materials_bp.route("/api/<int:mid>", methods=["GET"])
@login_required
def get_api(mid: int):
    """Single material — cross-class and nonexistent both 404 (no distinction)."""
    user = get_current_user()
    try:
        item = db.get_material(mid, user["class_id"])
    except Exception:
        return _db_error()
    if item is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(item)


@materials_bp.route("/api/<int:mid>/download", methods=["GET"])
@login_required
def download_api(mid: int):
    """Download — 401 unauth, 404 cross-class or missing file."""
    user = get_current_user()
    try:
        item = db.get_material(mid, user["class_id"])
    except Exception:
        return _db_error()
    if item is None:
        return jsonify({"error": "not found"}), 404
    file_path = item["file_path"]
    if not os.path.isabs(file_path):
        file_path = os.path.join(Config.UPLOAD_DIR, os.path.basename(file_path))
    if not os.path.isfile(file_path):
        return jsonify({"error": "not found"}), 404
    return send_file(file_path, as_attachment=True)


@materials_bp.route("/api/upload", methods=["POST"])
@login_required
@role_required("teacher")
def upload_api():
    """Teacher-only upload: save → parse → dual-table insert (atomic)."""
    user = get_current_user()

    if "file" not in request.files:
        return jsonify({"error": "no file provided"}), 400
    file = request.files["file"]
    if not file or not file.filename:
        return jsonify({"error": "no file selected"}), 400

    title = (request.form.get("title") or "").strip() or os.path.splitext(file.filename)[0]

    # Extension whitelist (white list, not black list)
    _, ext = os.path.splitext(file.filename)
    ext = ext.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({"error": f"unsupported file type: {ext}"}), 400

    # Disk name: server-generated UUID — client filename is only for display
    os.makedirs(Config.UPLOAD_DIR, exist_ok=True)
    stored = f"{uuid.uuid4().hex}_{_safe_filename(file.filename)}"
    dest_path = os.path.join(Config.UPLOAD_DIR, stored)
    file.save(dest_path)

    # Parse — cleanup file on parse failure
    try:
        body_text = parse_file(dest_path)
    except ValueError as e:
        try:
            os.remove(dest_path)
        except OSError:
            pass
        return jsonify({"error": str(e)}), 400

    # Dual-table insert in ONE transaction; clean up orphan file on DB failure
    knowledge_entry_id = None
    try:
        with db.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO materials (title, class_id, uploader_id, file_path) "
                "VALUES (%s, %s, %s, %s)",
                (title, user["class_id"], user["id"], dest_path),
            )
            material_id = cur.lastrowid
            cur.execute(
                "INSERT INTO knowledge_entries (material_id, class_id, body_text) "
                "VALUES (%s, %s, %s)",
                (material_id, user["class_id"], body_text),
            )
            knowledge_entry_id = cur.lastrowid
    except Exception:
        try:
            os.remove(dest_path)
        except OSError:
            pass
        return _db_error()

    # 上传后流程（切分 + 嵌入 + Qdrant）——独立于双表事务；
    # 嵌入失败不回滚材料记录 (spec line 38-42)
    strategy = (request.form.get("strategy") or None)
    try:
        _post_upload_indexing(material_id, knowledge_entry_id,
                              user["class_id"], body_text, strategy)
    except Exception:
        # 索引失败不影响上传成功响应；chunks 表中已 pending 的会保留
        pass

    return jsonify({"ok": True, "material_id": material_id}), 201


@materials_bp.route("/api/<int:mid>/reindex", methods=["POST"])
@login_required
@role_required("teacher")
def reindex_api(mid: int):
    """Teacher-only reindex — deletes old chunks + vectors, re-indexes with chosen strategy.
    Caller must be teacher of that material's class_id."""
    user = get_current_user()
    strategy = (request.args.get("strategy") or None)
    try:
        result = _reindex_indexing(mid, user["class_id"], strategy)
    except Exception:
        return _db_error()
    if isinstance(result, tuple):
        return jsonify(result[0]), result[1]
    return jsonify({"ok": True, "material_id": mid,
                    "n_ready": result.get("n_ready", 0),
                    "n_failed": result.get("n_failed", 0)}), 200
