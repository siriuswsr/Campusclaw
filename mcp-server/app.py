"""自建 MCP 工具服务器（JSON-RPC 2.0 over HTTP + Bearer 鉴权）。

独立小服务，与主应用同仓、由 docker-compose.yml 一并启动，容器端口 8090。
主应用通过 Base URL（http://mcp-demo:8090）真实连接，而不是 mock 主机侧逻辑。

端点：
    GET  /health   -> {"status": "ok"}
    POST /mcp      -> JSON-RPC 2.0
          initialize  / tools/list / tools/call

鉴权：请求头必须带 Authorization: Bearer <MCP_AUTH_TOKEN>；令牌只从环境变量读取，
不进 Git、不进镜像层、不进前端。缺失或错误一律 401。
"""
import json
import os
import re

from flask import Flask, jsonify, request

app = Flask(__name__)

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "campus-mcp-demo"
SERVER_VERSION = "1.0.0"
AUTH_TOKEN = os.environ.get("MCP_AUTH_TOKEN", "")

# ---------------------------------------------------------------------------
# 工具实现
# ---------------------------------------------------------------------------

FORMULA_HINTS = {
    "分数": "【公式提示】分数运算：a/b + c/d = (ad + bc) / bd；约分时分子分母同除以最大公因数。",
    "通分": "【公式提示】通分：先求分母的最小公倍数 L，再把每个分数化为以 L 为分母的等值分数。",
    "一元一次方程": "【公式提示】一元一次方程 ax + b = 0（a≠0）→ x = -b/a；步骤：移项变号、合并同类项、系数化为 1。",
    "方程": "【公式提示】一元一次方程 ax + b = 0（a≠0）→ x = -b/a。",
    "勾股定理": "【公式提示】勾股定理：直角三角形 a² + b² = c²（c 为斜边）。",
    "速度": "【公式提示】行程问题：路程 s = v·t，速度 v = s/t，时间 t = s/v。",
    "面积": "【公式提示】面积：长方形 S = ab；三角形 S = ½ah；圆 S = πr²。",
    "春晓": "【提示】《春晓》是孟浩然五言绝句：春眠不觉晓，处处闻啼鸟。夜来风雨声，花落知多少。",
}

UNIT_FACTORS = {
    # 长度，基准：毫米
    "mm": ("length", 1.0), "毫米": ("length", 1.0),
    "cm": ("length", 10.0), "厘米": ("length", 10.0),
    "dm": ("length", 100.0), "分米": ("length", 100.0),
    "m": ("length", 1000.0), "米": ("length", 1000.0),
    "km": ("length", 1000000.0), "千米": ("length", 1000000.0), "公里": ("length", 1000000.0),
    # 质量，基准：克
    "g": ("mass", 1.0), "克": ("mass", 1.0),
    "kg": ("mass", 1000.0), "千克": ("mass", 1000.0), "公斤": ("mass", 1000.0),
    "t": ("mass", 1000000.0), "吨": ("mass", 1000000.0),
}

_CONVERT_RE = re.compile(
    r"(-?\d+(?:\.\d+)?)\s*([^\s,，。？?]+?)\s*"
    r"(?:等于|转换成|转成|换成|转为|换算成|to|=|->)\s*"
    r"([^\s,，。？?]+)"
)


def _clean_unit(text: str) -> str:
    """去掉「多少 / 几 / 呢 / 啊」等口语填充词，只留单位本身。"""
    return re.sub(r"[多少几呢啊呀吧]", "", (text or "").strip())


def _format_number(value: float) -> str:
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text or "0"


def formula_hint(arguments: dict) -> dict:
    """输入知识点名，返回对应的公式提示文本。"""
    point = (arguments.get("knowledge_point") or arguments.get("query") or "").strip()
    if not point:
        return {"content": [{"type": "text", "text": "请提供知识点名称（knowledge_point）。"}],
                "isError": True}

    # 精确优先，其次包含匹配（如「一元一次方程怎么解」）
    if point in FORMULA_HINTS:
        return {"content": [{"type": "text", "text": FORMULA_HINTS[point]}]}
    for key, hint in FORMULA_HINTS.items():
        if key in point:
            return {"content": [{"type": "text", "text": hint}]}
    return {"content": [{"type": "text",
                         "text": f"暂未收录「{point}」的公式提示，请先回顾教材对应章节。"}]}


def unit_convert(arguments: dict) -> dict:
    """输入数值、源单位、目标单位，返回换算结果文本。"""
    value = arguments.get("value")
    from_unit = _clean_unit(arguments.get("from_unit") or "")
    to_unit = _clean_unit(arguments.get("to_unit") or "")

    # 允许只给一段自然语言查询，例如「3米等于多少厘米」
    if value is None or not from_unit or not to_unit:
        query = (arguments.get("query") or "").strip()
        m = _CONVERT_RE.search(query)
        if not m:
            return {"content": [{"type": "text",
                                 "text": "请提供数值、源单位与目标单位，例如：3 米 转 厘米。"}],
                    "isError": True}
        value = float(m.group(1))
        from_unit = _clean_unit(m.group(2))
        to_unit = _clean_unit(m.group(3))

    try:
        value = float(value)
    except (TypeError, ValueError):
        return {"content": [{"type": "text", "text": "数值必须是数字。"}], "isError": True}

    src = UNIT_FACTORS.get(from_unit.lower())
    dst = UNIT_FACTORS.get(to_unit.lower())
    if src is None:
        return {"content": [{"type": "text", "text": f"不支持的单位：{from_unit}"}], "isError": True}
    if dst is None:
        return {"content": [{"type": "text", "text": f"不支持的单位：{to_unit}"}], "isError": True}
    if src[0] != dst[0]:
        return {"content": [{"type": "text",
                             "text": f"{from_unit} 与 {to_unit} 不是同一类单位，无法换算。"}],
                "isError": True}

    result = value * src[1] / dst[1]
    text = f"{_format_number(value)} {from_unit} = {_format_number(result)} {to_unit}"
    return {"content": [{"type": "text", "text": text}]}


TOOL_HANDLERS = {
    "formula_hint": formula_hint,
    "unit_convert": unit_convert,
}

TOOLS = [
    {
        "name": "formula_hint",
        "description": "按知识点名返回公式提示文本（如「一元一次方程」「分数」「勾股定理」）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "knowledge_point": {"type": "string", "description": "知识点名称"},
                "query": {"type": "string", "description": "自然语言查询（knowledge_point 的替代）"},
            },
            "required": ["knowledge_point"],
        },
    },
    {
        "name": "unit_convert",
        "description": "数值单位换算（长度：mm/cm/m/km；质量：g/kg/t），返回换算结果文本。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "value": {"type": "number", "description": "数值"},
                "from_unit": {"type": "string", "description": "源单位，如 米 / m"},
                "to_unit": {"type": "string", "description": "目标单位，如 厘米 / cm"},
                "query": {"type": "string", "description": "自然语言查询，如「3米等于多少厘米」"},
            },
            "required": ["value", "from_unit", "to_unit"],
        },
    },
]


# ---------------------------------------------------------------------------
# JSON-RPC 2.0
# ---------------------------------------------------------------------------

def _ok(req_id, result):
    return jsonify({"jsonrpc": "2.0", "id": req_id, "result": result})


def _rpc_error(req_id, code, message, status=200):
    return jsonify({"jsonrpc": "2.0", "id": req_id,
                    "error": {"code": code, "message": message}}), status


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/mcp")
def mcp():
    # 1) Bearer 鉴权：令牌缺失或错误一律 401，不透露内部细节
    if not AUTH_TOKEN:
        return jsonify({"error": "server token not configured"}), 401
    auth = request.headers.get("Authorization", "")
    expected = f"Bearer {AUTH_TOKEN}"
    if auth != expected:
        return jsonify({"error": "unauthorized"}), 401

    # 2) 解析 JSON-RPC 信封
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _rpc_error(None, -32700, "Parse error: body must be a JSON object", status=400)

    req_id = payload.get("id")
    if payload.get("jsonrpc") != "2.0":
        return _rpc_error(req_id, -32600, "Invalid Request: jsonrpc must be \"2.0\"")
    method = payload.get("method")
    if not isinstance(method, str) or not method:
        return _rpc_error(req_id, -32600, "Invalid Request: method is required")
    params = payload.get("params") or {}
    if not isinstance(params, dict):
        return _rpc_error(req_id, -32602, "Invalid params: must be an object")

    # 3) 方法路由
    if method == "initialize":
        return _ok(req_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })

    if method == "tools/list":
        return _ok(req_id, {"tools": TOOLS})

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str) or name not in TOOL_HANDLERS:
            return _rpc_error(req_id, -32602, f"Unknown tool: {name}")
        if not isinstance(arguments, dict):
            return _rpc_error(req_id, -32602, "Invalid params: arguments must be an object")
        try:
            return _ok(req_id, TOOL_HANDLERS[name](arguments))
        except Exception as e:  # 工具自身异常也要是可读的 JSON-RPC error
            return _rpc_error(req_id, -32603, f"Tool execution failed: {e}")

    return _rpc_error(req_id, -32601, f"Method not found: {method}")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("MCP_PORT", "8090")))
