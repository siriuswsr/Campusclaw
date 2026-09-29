"""CampusClaw Flask application factory."""
import os
from flask import Flask, jsonify, redirect, request, url_for

from .config import Config


def create_app():
    Config.validate()

    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(__file__), "templates"),
    )
    app.config["SECRET_KEY"] = Config.SECRET_KEY
    app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024

    # Explicit cookie policy (verifyable, not relying on Flask defaults)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

    os.makedirs(Config.UPLOAD_DIR, exist_ok=True)

    # Register blueprints
    from .auth import auth_bp
    from .materials import materials_bp
    from .search_routes import search_bp
    from .qa import qa_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(materials_bp)
    app.register_blueprint(search_bp)
    app.register_blueprint(qa_bp)

    # Root → list when logged in, otherwise straight to the login page
    @app.route("/")
    def root():
        from .auth import get_current_user
        if not get_current_user():
            return redirect(url_for("auth.login_page"))
        return redirect(url_for("materials.list_page"))

    # Convenience alias so the login page is also reachable at /login.
    # Canonical implementation stays in the auth blueprint (no duplicated logic).
    @app.route("/login", methods=["GET", "POST"])
    def login_alias():
        from .auth import login_page, login_post
        return login_post() if request.method == "POST" else login_page()

    # /health — PURE liveness, NO DB check. DB jitter must NOT be misread
    # as container failure by the orchestrator.
    @app.get("/health")
    def health():
        return jsonify({"status": "ok"}), 200

    return app
