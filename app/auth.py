"""JWT authentication — token issuing, verification, request-scoped identity.

There is NO Flask session anywhere: identity lives in the JWT carried by the
`Authorization: Bearer <token>` header and is injected into `request.user` by
@require_auth. Pages are client-rendered shells (see app/static/auth.js) — the
browser attaches the token to every /api call.
"""
import time
import threading
from functools import wraps

import jwt
from flask import Blueprint, request, jsonify, redirect, url_for, render_template

from . import db
from .config import Config
from .openapi import doc
from .security import verify_password

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")
auth_api_bp = Blueprint("auth_api", __name__, url_prefix="/api")

# Expired AND tampered/invalid tokens share one opaque body (no oracle for
# attackers, one predictable contract for the client).
TOKEN_ERROR = {"error": "token_expired"}


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
# Token helpers
# ---------------------------------------------------------------------------

def issue_token(user: dict) -> str:
    """Sign an HS256 token: {user_id, role, class_id, exp:+24h timestamp}."""
    payload = {
        "user_id": user["id"],
        "role": user["role"],
        "class_id": user["class_id"],
        "exp": int(time.time()) + Config.JWT_TTL_HOURS * 3600,
    }
    return jwt.encode(payload, Config.JWT_SECRET, algorithm=Config.JWT_ALGORITHM)


def _bearer_token():
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    token = header[len("Bearer "):].strip()
    return token or None


def require_auth(view):
    """Decode the Bearer token, inject request.user; 401 otherwise.

    Every failure mode (missing header, expired, tampered, wrong signature)
    returns 401 {"error": "token_expired"} — see TOKEN_ERROR.
    """
    @wraps(view)
    def wrapper(*args, **kwargs):
        token = _bearer_token()
        if not token:
            return jsonify(TOKEN_ERROR), 401
        try:
            payload = jwt.decode(token, Config.JWT_SECRET,
                                 algorithms=[Config.JWT_ALGORITHM])
        except jwt.PyJWTError:
            return jsonify(TOKEN_ERROR), 401
        request.user = payload
        return view(*args, **kwargs)
    return wrapper


def get_current_user():
    """Normalized identity from the verified token — never from client input."""
    payload = getattr(request, "user", None)
    if not payload:
        return None
    user = {
        "id": payload["user_id"],
        "role": payload["role"],
        "class_id": payload["class_id"],
    }
    # username is display-only: a DB hiccup must NOT invalidate a valid token
    try:
        row = db.get_user_by_id(payload["user_id"])
        user["username"] = (row or {}).get("username")
    except Exception:
        user["username"] = None
    return user


def role_required(*allowed_roles):
    """Decorator: 403 if the token's role is not in allowed_roles."""
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            user = get_current_user()
            if not user:
                return jsonify(TOKEN_ERROR), 401
            if user["role"] not in allowed_roles:
                return jsonify({"error": "forbidden"}), 403
            return view(*args, **kwargs)
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@auth_bp.route("/login", methods=["GET"])
def login_page():
    """Login page shell. The client bounces to /materials when a token exists —
    the server cannot know, since it never receives the localStorage token on
    a plain browser navigation."""
    return render_template("login.html")


@auth_bp.route("/logout", methods=["GET", "POST"])
def logout_page():
    """Nothing to clear server-side (no session): the client drops the token."""
    return redirect(url_for("auth.login_page"))


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@doc(summary="登录", description="返回 JWT（HS256，24h）与用户信息；密码错误/用户不存在/被限流一律同构 401。",
     tags=["auth"], security=False,
     request_body={"schema": {"type": "object", "required": ["username", "password"],
                              "properties": {"username": {"type": "string"},
                                             "password": {"type": "string"}}}},
     responses={
         200: {"description": "OK", "schema": {"type": "object", "properties": {
             "token": {"type": "string"},
             "user": {"type": "object", "properties": {
                 "id": {"type": "integer"}, "name": {"type": "string"},
                 "role": {"type": "string"}, "class_id": {"type": "integer"}}}}}},
         401: {"description": "凭证无效", "schema": {"$ref": "#/components/schemas/Error"}},
     })
@auth_api_bp.route("/login", methods=["POST"])
def login_post():
    """POST /api/login → {"token", "user"}.

    Failure responses are uniform across no-such-user / wrong password /
    rate-limited: 401 with an identical body — no user enumeration.
    """
    data = request.get_json(silent=True) or request.form.to_dict()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    client_ip = request.remote_addr or "unknown"

    # --- Rate limit check ---
    if _is_rate_limited(client_ip, username):
        return jsonify({"error": "invalid credentials"}), 401

    # --- DB lookup ---
    try:
        user = db.get_user_by_username(username)
    except Exception:
        # DB unavailable → 503 (a valid token, if any, stays valid)
        _record_attempt(client_ip, username)
        return jsonify({"error": "service unavailable"}), 503

    # --- Uniform failure response for both branches ---
    if user is None or not verify_password(password, user["password_hash"]):
        _record_attempt(client_ip, username)
        return jsonify({"error": "invalid credentials"}), 401

    # --- Success ---
    with _login_lock:
        _login_attempts.pop((client_ip, username), None)

    return jsonify({
        "token": issue_token(user),
        "user": {
            "id": user["id"],
            "name": user["username"],
            "role": user["role"],
            "class_id": user["class_id"],
        },
    }), 200


@auth_api_bp.route("/me", methods=["GET"])
@auth_bp.route("/me", methods=["GET"])
@require_auth
def me():
    user = get_current_user()
    return jsonify({
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "class_id": user["class_id"],
    })


@auth_api_bp.route("/logout", methods=["GET", "POST"])
def logout_api():
    """Token is stateless — logout is purely client-side (drop localStorage)."""
    return jsonify({"ok": True}), 200