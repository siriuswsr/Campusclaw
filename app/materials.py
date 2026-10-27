"""Materials routes — list, get by id, upload (teacher only), download.
ALL data reads force class_id from the verified JWT — never trust client input.
Pages are client-rendered shells; the JSON APIs below do the filtering.
Post-upload indexing (chunks + embeddings + Qdrant) is in app.indexing.
"""
import os
import uuid
from flask import Blueprint, request, jsonify, send_file, render_template

from . import db
from .auth import require_auth, role_required, get_current_user
from .knowledge import parse_file, ALLOWED_EXTENSIONS
from .config import Config
from .indexing import post_upload as _post_upload_indexing, reindex as _reindex_indexing
from .openapi import doc

materials_bp = Blueprint("materials", __name__, url_prefix="/materials")

# 讲义的学科下拉选项：教师上传时必选其一。旧数据/未填写的行允许为空（NULL），
# 前端「全部」即不发 subject 参数。
SUBJECT_CHOICES = [
    "未分类", "语文", "数学", "物理", "化学", "英语", "历史",
    "地理", "生物", "劳动", "科学", "思政", "其它",
]


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
def list_page():
    """Client-rendered shell: the browser guards on localStorage and fills the
    table from GET /materials/api with its Bearer token."""
    return render_template("materials.html", SUBJECT_CHOICES=SUBJECT_CHOICES)


@materials_bp.route("/<int:mid>", methods=["GET"])
def detail_page(mid: int):
    """Client-rendered shell for 查看/下载.

    The page itself discloses nothing: existence + class isolation are decided
    by GET /materials/api/<mid>, which returns the same 404 for cross-class and
    nonexistent ids (the client renders 「材料不存在或无权访问」).
    """
    return render_template("material_detail.html", mid=mid)


# ---------------------------------------------------------------------------
# API routes (JSON)
# ---------------------------------------------------------------------------

@materials_bp.route("/api", methods=["GET"])
@require_auth
def list_api():
    """JSON list — class_id forced from the token; optional ?subject= filters.

    subject comes from the query string but is NOT a security field: it only
    narrows within the already-token-scoped class. Absent subject → all rows.
    """
    user = get_current_user()
    subject = (request.args.get("subject") or "").strip()
    try:
        items = db.list_materials(user["class_id"], subject or None)
    except Exception:
        return _db_error()
    return jsonify(items)


@materials_bp.route("/api/<int:mid>", methods=["GET"])
@require_auth
def get_api(mid: int):
    """Single material — cross-class and nonexistent both 404 (no distinction).

    Includes body_text so the client-rendered detail page can show 材料正文.
    """
    user = get_current_user()
    try:
        item = db.get_material(mid, user["class_id"])
        entry = db.get_knowledge_entry(mid, user["class_id"]) if item else None
    except Exception:
        return _db_error()
    if item is None:
        return jsonify({"error": "not found"}), 404
    data = dict(item)
    data["body_text"] = (entry or {}).get("body_text", "")
    return jsonify(data)


@materials_bp.route("/api/<int:mid>/download", methods=["GET"])
@require_auth
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


@doc(summary="上传讲义（教师）", description="multipart/form-data：file(.txt/.md 白名单) + title + subject + strategy。成功 201 返回 material_id；非教师 403。",
     tags=["materials"],
     request_body={"content_type": "multipart/form-data", "schema": {
         "type": "object", "required": ["file"],
         "properties": {
             "file": {"type": "string", "format": "binary"},
             "title": {"type": "string"},
             "subject": {"type": "string"},
             "strategy": {"type": "string", "enum": ["auto", "custom", "hierarchy"]},
         }}},
     responses={
         201: {"description": "已创建", "schema": {"type": "object", "properties": {
             "ok": {"type": "boolean"}, "material_id": {"type": "integer"}}}},
         400: {"description": "无文件/类型不符/学科非法", "schema": {"$ref": "#/components/schemas/Error"}},
         403: {"description": "非教师", "schema": {"$ref": "#/components/schemas/Error"}},
         503: {"description": "数据库不可用", "schema": {"$ref": "#/components/schemas/Error"}},
     })
@materials_bp.route("/api/upload", methods=["POST"])
@require_auth
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

    # 学科：前端要求必选（下拉框 required）。后端校验白名单；为兼容既有
    # 自带/不带 subject 的上传测试，缺失时保持 NULL（「未分类」等价于不过滤）。
    subject = (request.form.get("subject") or "").strip()
    if subject and subject not in SUBJECT_CHOICES:
        return jsonify({"error": "invalid subject"}), 400
    subject = subject or None

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
                "INSERT INTO materials (title, subject, class_id, uploader_id, file_path) "
                "VALUES (%s, %s, %s, %s, %s)",
                (title, subject, user["class_id"], user["id"], dest_path),
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
@require_auth
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
