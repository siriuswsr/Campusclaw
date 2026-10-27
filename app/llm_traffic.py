"""LLM 网关流量日志（logs/llm_traffic.jsonl）。

每次向 LLM 网关（chat.completions）发请求前记一条 request，响应结束后记一条
response；一行一条 JSON。scene 取自入站请求头 X-Debug-Scene，没有就写空串。

硬约束：写日志失败绝不阻塞主流程 —— 所有磁盘操作都在 try/except 里吞掉异常。
"""
import json
import os
import threading
from datetime import datetime

from flask import has_request_context, request

SCENE_HEADER = "X-Debug-Scene"

# 仓库根（容器内 /app）下的 logs/；与宿主机的 d:\...\campusclaw-mygroup\logs 对应。
LOG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
LOG_FILE = os.path.join(LOG_DIR, "llm_traffic.jsonl")

# gunicorn 单 worker 下仍有批改线程等并发写入，加锁避免行与行交错。
_write_lock = threading.Lock()


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def current_scene() -> str:
    """入站请求头 X-Debug-Scene；没有请求上下文（后台线程/流式生成器）时为空。"""
    try:
        if has_request_context():
            return (request.headers.get(SCENE_HEADER) or "").strip()
    except Exception:
        pass
    return ""


def _append(record: dict) -> None:
    """追加一行 JSON；任何失败（磁盘满、权限、编码）都静默忽略。"""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False)
        with _write_lock:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass


def log_request(scene: str, method: str, url: str, body) -> None:
    """请求体原样落盘（model / messages / 其他参数一项不改）。"""
    _append({
        "timestamp": _now(),
        "type": "request",
        "scene": scene or "",
        "method": method,
        "url": url,
        "body": body,
    })


def log_response(scene: str, response: str) -> None:
    """响应结束后的完整文本（SSE 为所有 delta 拼接结果）。"""
    _append({
        "timestamp": _now(),
        "type": "response",
        "scene": scene or "",
        "response": response,
    })
