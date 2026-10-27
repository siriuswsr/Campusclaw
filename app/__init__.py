"""CampusClaw Flask application factory."""
import os
from flask import Flask, jsonify, redirect, render_template, request, url_for

from .config import Config


def create_app():
    Config.validate()

    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(__file__), "templates"),
    )
    app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024

    os.makedirs(Config.UPLOAD_DIR, exist_ok=True)

    # Register blueprints
    from .auth import auth_bp, auth_api_bp
    from .materials import materials_bp
    from .search_routes import search_bp
    from .qa import qa_bp
    from .assistant import assistant_bp
    from .agents import agents_bp
    from .skills import skills_bp
    from .sessions import sessions_bp
    from .openapi import register_openapi
    from .lectures import lectures_bp
    from .pages import pages_bp
    from .assignments import assignments_bp
    from .submissions import submissions_bp
    from .wrong import wrong_bp
    from .mcp import mcp_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(auth_api_bp)
    app.register_blueprint(materials_bp)
    app.register_blueprint(search_bp)
    app.register_blueprint(qa_bp)
    app.register_blueprint(assistant_bp)
    app.register_blueprint(agents_bp)
    app.register_blueprint(skills_bp)
    app.register_blueprint(sessions_bp)
    app.register_blueprint(lectures_bp)
    app.register_blueprint(pages_bp)
    # Lesson 6 — homework flow (all three share the /api prefix)
    app.register_blueprint(assignments_bp)
    app.register_blueprint(submissions_bp)
    app.register_blueprint(wrong_bp)
    # Lesson 8 — MCP server registry + tool discovery
    app.register_blueprint(mcp_bp)

    # OpenAPI + Swagger（提示词 H）—— 在业务路由注册之后挂载，/openapi.json
    # 现场扫描 url_map + 各 handler 上的 @doc 注解，文档与代码同源。
    register_openapi(app)

    # Root → login page. The server holds no identity (no session, no cookie),
    # so the "already logged in" decision belongs to the client guard, which
    # bounces straight to /materials when a token is present in localStorage.
    @app.route("/")
    def root():
        return redirect(url_for("auth.login_page"))

    # Convenience alias so the login page is also reachable at /login.
    # Canonical implementation stays in the auth blueprint (no duplicated logic).
    @app.route("/login", methods=["GET", "POST"])
    def login_alias():
        from .auth import login_page, login_post
        return login_post() if request.method == "POST" else login_page()

    # Teacher assistant-config page — same shell contract as the other pages:
    # always 200, the client guard decides (no token → /login). Role is NOT
    # enforced here; every /api/assistant/* call behind it is @require_teacher.
    @app.get("/teacher/assistant-config")
    def teacher_assistant_config():
        return render_template("teacher_assistant_config.html")

    # Student assistant chat page — same shell contract (always 200, client
    # guard). /api/assistant/ask behind it only needs a valid token.
    @app.get("/student/assistant")
    def student_assistant():
        return render_template("student_assistant.html")

    # /health — PURE liveness, NO DB check. DB jitter must NOT be misread
    # as container failure by the orchestrator.
    @app.get("/health")
    def health():
        return jsonify({"status": "ok"}), 200

    return app
