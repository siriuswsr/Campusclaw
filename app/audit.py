"""统一审计入口（design.md D6）：写入接口挂审计钩子，第 9 课按 action 查这张表。

调用方只传 action 与 target_id，需要留痕的额外信息以关键字参数传入，例如：

    record_audit("teacher_grade", sid, ai_score=old, final_score=85)

两条硬约束：
1. 身份取自本仓库的 JWT（app/auth.py 的 get_current_user()），不是 flask_login 的
   current_user —— 本 change 不改认证方式；未登录时 user_id 记 0。
2. 审计写入失败 MUST NOT 阻塞主业务：整段包 try/except，只记 warning。
"""
import json

from flask import current_app, has_request_context

from . import db
from .auth import get_current_user


def record_audit(action: str, target_id: int | None = None, **fields) -> None:
    """写一行 audit_log。永不抛异常，永不回滚主业务的事务。"""
    try:
        # 批改线程里没有请求上下文，get_current_user() 会读 request.user 而报错，
        # 这里先判上下文，避免把「线程里没有身份」误报成审计失败。
        user = (get_current_user() or {}) if has_request_context() else {}
        with db.get_conn() as conn:
            db._exec(
                conn,
                "INSERT INTO audit_log "
                "(user_id, class_id, role, action, target_id, fields) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    user.get("id") or 0,
                    user.get("class_id"),
                    user.get("role"),
                    action,
                    target_id,
                    json.dumps(fields, ensure_ascii=False) if fields else None,
                ),
            )
    except Exception as e:
        current_app.logger.warning("audit_log write failed: action=%s, err=%s", action, e)