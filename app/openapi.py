"""最小 OpenAPI 生成器 + Swagger UI（提示词 H）。

不引入 flask-openapi3 / apispec：直接用一个 @doc(...) 装饰器把注解挂在视图函数上，
再由 Flask 的 url_map 现场生成 OpenAPI 3.0 文档 —— 文档与代码同源，改了 handler
的注解，重新打开 /openapi.json 即反映。

    @doc(summary="列出本班智能体", tags=["agents"], responses={200: {...}})
    def list_agents(): ...

挂载：
    GET /openapi.json   现场生成的 spec
    GET /swagger        Swagger UI（从 CDN 载入静态资源；顺便直链 /openapi.json）
"""
from flask import Blueprint, Response, jsonify

openapi_bp = Blueprint("openapi", __name__)


def doc(summary: str, description: str = None, tags=None,
        request_body=None, responses=None, parameters=None, security=True):
    """把一个视图函数标注为 OpenAPI 文档的一部分。

    request_body: {"content_type": "application/json", "schema": {...}}
    responses:    {200: {"description": "...", "schema": {...}}, ...}
    parameters:   [{"name": ..., "in": "query|path|header", "required": bool,
                    "schema": {...}, "description": ...}]
    """
    def deco(view):
        view.__cc_doc__ = {
            "summary": summary,
            "description": description,
            "tags": tags or [],
            "request_body": request_body,
            "responses": responses or {200: {"description": "OK"}},
            "parameters": parameters or [],
            "security": security,
        }
        return view
    return deco


def _module_of(endpoint: str) -> str:
    return (endpoint or "").split(".")[0]


def build_spec(app) -> dict:
    paths: dict = {}
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        view = app.view_functions.get(rule.endpoint)
        meta = getattr(view, "__cc_doc__", None)
        if not meta:
            continue  # 未注解的接口不进文档（按批补注解）

        path = str(rule)
        if not path.startswith("/"):
            path = "/" + path
        # <int:agent_id> → {agent_id}
        path_params = []
        for arg in sorted(rule.arguments):
            path = path.replace(f"<int:{arg}>", "{%s}" % arg)
            path = path.replace(f"<float:{arg}>", "{%s}" % arg)
            path = path.replace(f"<string:{arg}>", "{%s}" % arg)
            path = path.replace(f"<path:{arg}>", "{%s}" % arg)
            path = path.replace(f"<{arg}>", "{%s}" % arg)
            path_params.append({
                "name": arg, "in": "path", "required": True,
                "schema": {"type": "string"}, "description": arg,
            })

        methods = sorted((rule.methods or set()) - {"HEAD", "OPTIONS"})
        entry = paths.setdefault(path, {})
        for method in methods:
            op = {
                "summary": meta["summary"],
                "tags": meta["tags"] or [_module_of(rule.endpoint)],
                "responses": {
                    str(code): {
                        "description": spec.get("description", ""),
                        "content": {"application/json": {"schema": spec.get("schema", {})}},
                    }
                    for code, spec in meta["responses"].items()
                },
            }
            if meta.get("description"):
                op["description"] = meta["description"]
            params = list(path_params) + list(meta["parameters"])
            # 同名 path 参数去重（自动生成的优先）
            seen = set()
            uniq = []
            for p in params:
                if p["name"] in seen:
                    continue
                seen.add(p["name"])
                uniq.append(p)
            if uniq:
                op["parameters"] = uniq
            rb = meta.get("request_body")
            if rb:
                ctype = rb.get("content_type", "application/json")
                op["requestBody"] = {
                    "required": rb.get("required", True),
                    "content": {ctype: {"schema": rb.get("schema", {})}},
                }
            if meta.get("security"):
                op["security"] = [{"bearerAuth": []}]
            entry[method.lower()] = op

    return {
        "openapi": "3.0.3",
        "info": {
            "title": "CampusClaw API",
            "version": "1.0.0",
            "description": "辅导智能体 / 技能 / 会话 / 检索接口（含本课新增接口）。",
        },
        "servers": [{"url": "/"}],
        "components": {
            "securitySchemes": {
                "bearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"},
            },
            "schemas": {
                "Error": {
                    "type": "object",
                    "properties": {
                        "error": {"type": "string"},
                        "message": {"type": "string"},
                    },
                },
            },
        },
        "paths": paths,
    }


_SWAGGER_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>CampusClaw API — Swagger</title>
<link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css">
<style>body{margin:0;font-family:system-ui,"Microsoft YaHei",sans-serif}
#fallback{padding:24px;max-width:720px}#fallback code{background:#f2f2f2;padding:2px 6px;border-radius:4px}</style>
</head>
<body>
<div id="fallback">
  <h1>CampusClaw API</h1>
  <p>Swagger UI 静态资源自 CDN 载入；离线时可直接查看
     <a href="/openapi.json">/openapi.json</a>（OpenAPI 3.0，含本课全部接口）。</p>
  <p>带鉴权的接口请先在 <code>POST /api/login</code> 取得 token，再点右上角
     <b>Authorize</b> 填入 <code>&lt;token&gt;</code>（Bearer）。</p>
</div>
<div id="swagger-ui"></div>
<script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
<script>
if (window.SwaggerUIBundle) {
  document.getElementById('fallback').style.display = 'none';
  window.SwaggerUIBundle({
    url: '/openapi.json',
    dom_id: '#swagger-ui',
    deepLinking: true,
    persistAuthorization: true,
  });
}
</script>
</body>
</html>
"""


@openapi_bp.route("/openapi.json", methods=["GET"])
def openapi_json():
    from flask import current_app
    return jsonify(build_spec(current_app))


@openapi_bp.route("/swagger", methods=["GET"])
def swagger_ui():
    return Response(_SWAGGER_HTML, mimetype="text/html; charset=utf-8")


def register_openapi(app):
    app.register_blueprint(openapi_bp)
