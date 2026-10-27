"""技能路由（提示词 D）—— 词法计分，P0 版。

给定一个智能体（含它绑定的技能列表）与用户输入文本，按适用条件词的命中数
给每个启用技能打分，返回命中数最高的技能（分数相同则编号小者优先）；没有任何
技能命中时返回 None。

本仓库不使用 ORM，`agent` 以 dict 传入，其 "skills" 键是技能 dict 列表
（由 db.list_agent_skills 给出）。为兼容"对象带 .skills"的写法，同时做了
两者判断。"""
import re

# 触发词表按中文/英文逗号、顿号、分号、竖线切。
_TRIGGER_SPLIT_RE = re.compile(r"[,，、;；|]")


def route_skill(agent, user_text):
    """返回命中的技能 dict，或 None。"""
    if agent is None:
        return None
    skills = agent["skills"] if isinstance(agent, dict) else getattr(agent, "skills", [])
    candidates = [s for s in (skills or []) if s.get("enabled")]
    if not candidates:
        return None

    raw_text = user_text or ""

    scored = []
    for s in candidates:
        triggers = _TRIGGER_SPLIT_RE.split(s.get("trigger_text") or "")
        # 命中判定：触发词作为子串出现在提问里即可（中文没有空格分词，
        # 「勾股定理是什么」也应命中触发词「勾股定理」）。
        score = sum(1 for t in triggers if t and t in raw_text)
        if score > 0:
            scored.append((score, s["id"], s))

    if not scored:
        return None

    scored.sort(key=lambda x: (-x[0], x[1]))  # 分数高优先，同分编号小优先
    return scored[0][2]