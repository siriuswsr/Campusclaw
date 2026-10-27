"""技能文件解析器（提示词 C）。

把一段「元数据区 + 空行 + 正文区」的技能文件文本解析成结构化 dict，
供 app/skills.py 校验后落库。所有校验失败都抛 ValueError，消息是可读中文，
由 skills.py 转成 400 响应返回给教师。

元数据区字段（每行「字段：值」）：
    名称      必填
    适用条件  必填        触发器词，逗号/顿号等分隔
    资料      可选        学科来源
    工具      可选        knowledge_search 或 MCP 引用 工具名@服务器名
    启用      必填        是|否

返回 dict 键：name / trigger_text / subject_text / tools_json / enabled
             / description（正文区）/ source_text（原文）。
"""
import json
import re

ALLOWED_FIELDS = {"名称", "适用条件", "资料", "工具", "启用"}
REQUIRED_FIELDS = ["名称", "适用条件", "启用"]
MAX_LENGTH = 8000

KNOWLEDGE_TOOL = "knowledge_search"

# 字段分隔：中文冒号或英文冒号（首次出现处切分）
_FIELD_SPLIT_RE = re.compile(r"[：:]")
_TOOL_SPLIT_RE = re.compile(r"[,，、;；\s]+")

# MCP 引用格式 工具名@服务器名：
#   工具名 —— 小写字母开头，可含下划线、数字，无连字符（提示词 A）
#   服务器名 —— 登记表里的名字，不含空白与 @
_TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_SERVER_NAME_RE = re.compile(r"^[^\s@]+$")


def split_tool_ref(ref: str):
    """把一条工具声明拆成 (tool_name, server_name|None)。

    knowledge_search → ("knowledge_search", None)
    formula_hint@campus-tools → ("formula_hint", "campus-tools")
    非法格式抛 ValueError(可读消息)。
    """
    ref = (ref or "").strip()
    if not ref:
        raise ValueError("工具名不能为空")
    if ref == KNOWLEDGE_TOOL:
        return KNOWLEDGE_TOOL, None
    if "@" not in ref:
        raise ValueError(f"未定义工具：{ref}（仅支持 {KNOWLEDGE_TOOL} 或 工具名@服务器名）")

    parts = ref.split("@")
    if len(parts) != 2:
        raise ValueError(f"工具引用格式不正确：{ref}（应为 工具名@服务器名）")
    tool_name, server_name = parts[0].strip(), parts[1].strip()

    if not _TOOL_NAME_RE.match(tool_name):
        raise ValueError(f"MCP 工具名不合法：{tool_name}（须小写字母开头，可含下划线/数字，不能有连字符）")
    if not server_name or not _SERVER_NAME_RE.match(server_name):
        raise ValueError(f"MCP 服务器名不合法：{server_name}")
    return tool_name, server_name


def mcp_tool_refs(tools_json: str) -> list[tuple[str, str]]:
    """从已解析的 tools_json 里取出 MCP 引用 [(工具名, 服务器名), ...]。"""
    try:
        refs = json.loads(tools_json or "[]")
    except Exception:
        return []
    out = []
    for ref in refs:
        try:
            tool_name, server_name = split_tool_ref(ref)
        except ValueError:
            continue
        if server_name:
            out.append((tool_name, server_name))
    return out


def parse_skill(text: str) -> dict:
    """解析技能文件；不合法时抛 ValueError(可读消息)。"""
    if not isinstance(text, str):
        raise ValueError("技能文件内容必须是文本")

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if len(normalized) > MAX_LENGTH:
        raise ValueError(f"内容超过 {MAX_LENGTH} 字上限")

    # 按第一个空行切成「元数据区」和「正文区」
    parts = re.split(r"\n[ \t]*\n", normalized, maxsplit=1)
    meta_block = parts[0]
    body = parts[1] if len(parts) > 1 else ""

    meta: dict[str, str] = {}
    for raw in meta_block.split("\n"):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        pieces = _FIELD_SPLIT_RE.split(line, maxsplit=1)
        if len(pieces) != 2:
            raise ValueError(f"未定义字段：{line}")
        key = pieces[0].strip()
        val = pieces[1].strip()
        if key not in ALLOWED_FIELDS:
            raise ValueError(f"未定义字段：{key}")
        if key in meta:
            raise ValueError(f"字段「{key}」重复出现")
        meta[key] = val

    # 必填字段校验
    for field in REQUIRED_FIELDS:
        if field not in meta or not meta[field]:
            raise ValueError(f"{field}必填")

    # 启用：是|否
    enabled_raw = meta["启用"].strip()
    if enabled_raw == "是":
        enabled = True
    elif enabled_raw == "否":
        enabled = False
    else:
        raise ValueError("启用只能是 是 或 否")

    # 工具：knowledge_search 或 工具名@服务器名（服务器归属/连接状态由 skills.py 校验）
    if "工具" in meta and meta["工具"].strip():
        raw_tools = [t for t in _TOOL_SPLIT_RE.split(meta["工具"]) if t]
        tools = []
        for ref in raw_tools:
            split_tool_ref(ref)  # 只校验格式
            if ref not in tools:
                tools.append(ref)
        tools_json = json.dumps(tools, ensure_ascii=False)
    else:
        tools_json = json.dumps([], ensure_ascii=False)

    # 正文区不能为空
    description = body.strip()
    if not description:
        raise ValueError("正文区为空")

    return {
        "name": meta["名称"].strip(),
        "trigger_text": meta["适用条件"].strip(),
        "subject_text": (meta.get("资料") or "").strip() or None,
        "tools_json": tools_json,
        "enabled": enabled,
        "description": description,
        "source_text": normalized,
    }