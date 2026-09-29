"""Authentication routes and session helpers."""
import time
import threading
from functools import wraps
from flask import Blueprint, request, session, redirect, url_for, jsonify, render_template

from . import db
from .security import verify_password

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


# ---------------------------------------------------------------------------
# Login rate limiting — in-memory, per (IP, username) sliding window
# ---------------------------------------------------------------------------
MAX_LOGIN_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 60

_login_attempts: dict[tuple[str, str], list[float]] = {}
_login_lock = threading.Lock()


def _is_rate_limited(ip: str, username: str) -> bool:
    cutoff = time.time() - LOGIN_WINDOW_SECONDS
    with _login_lock:
        key = (ip, username)
        recent = [t for t in _login_attempts.get(key, []) if t > cutoff]
        return len(recent) >= MAX_LOGIN_ATTEMPTS


def _record_attempt(ip: str, username: str) -> None:
    cutoff = time.time() - LOGIN_WINDOW_SECONDS
    with _login_lock:
        key = (ip, username)
        recent = [t for t in _login_attempts.get(key, []) if t > cutoff]
        recent.append(time.time())
        _login_attempts[key] = recent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_json_request():
    return (
        "/api" in request.path
        or request.headers.get("Accept", "").startswith("application/json")
        or request.is_json
    )


def get_current_user():
    """Return the logged-in user dict or None."""
    uid = session.get("user_id")
    if not uid:
        return None
    try:
        return db.get_user_by_id(uid)
    except Exception:
        return None


def login_required(view):
    """Decorator: redirect page requests to /login, return 401 for JSON API."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not get_current_user():
            if _is_json_request():
                return jsonify({"error": "unauthorized"}), 401
            return redirect(url_for("auth.login_page", next=request.path))
        return view(*args, **kwargs)
    return wrapper


def role_required(*allowed_roles):
    """Decorator: 403 if current user's role is not in allowed_roles."""
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            user = get_current_user()
            if not user:
                return jsonify({"error": "unauthorized"}), 401
            if user["role"] not in allowed_roles:
                return jsonify({"error": "forbidden"}), 403
            return view(*args, **kwargs)
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@auth_bp.route("/login", methods=["GET"])
def login_page():
    if get_current_user():
        return redirect(url_for("materials.list_page"))
    return render_template("login.html")


@auth_bp.route("/login", methods=["POST"])
def login_post():
    """Authenticate user.

    Failure responses are uniform across:
      - no such user
      - wrong password
      - rate-limited
    All return 401 with identical body — no user enumeration.
    """
    data = request.get_json(silent=True) or request.form.to_dict()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    client_ip = request.remote_addr or "unknown"

    # --- Rate limit check ---
    if _is_rate_limited(client_ip, username):
        if _is_json_request():
            return jsonify({"error": "invalid credentials"}), 401
        return render_template("login.html", error="用户名或密码错误"), 401

    # --- DB lookup ---
    try:
        user = db.get_user_by_username(username)
    except Exception:
        # DB unavailable → 503 (NOT session invalidation)
        _record_attempt(client_ip, username)
        if _is_json_request():
            return jsonify({"error": "service unavailable"}), 503
        return render_template("login.html", error="服务暂时不可用，请稍后重试"), 503

    # --- Uniform failure response for both branches ---
    if user is None or not verify_password(password, user["password_hash"]):
        _record_attempt(client_ip, username)
        if _is_json_request():
            return jsonify({"error": "invalid credentials"}), 401
        return render_template("login.html", error="用户名或密码错误"), 401

    # --- Success ---
    with _login_lock:
        _login_attempts.pop((client_ip, username), None)

    session.clear()
    session["user_id"] = user["id"]
    session["role"] = user["role"]
    session["class_id"] = user["class_id"]
    session["username"] = user["username"]

    next_url = request.args.get("next") or url_for("materials.list_page")
    if _is_json_request():
        return jsonify({"ok": True, "role": user["role"], "class_id": user["class_id"]}), 200
    return redirect(next_url)


@auth_bp.route("/logout", methods=["GET", "POST"])
def logout():
    session.clear()
    return redirect(url_for("auth.login_page"))


@auth_bp.route("/me", methods=["GET"])
@login_required
def me():
    user = get_current_user()
    return jsonify({
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "class_id": user["class_id"],
    })
