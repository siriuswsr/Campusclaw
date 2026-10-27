"""Lesson 6 — homework flow, part 3: wrong questions + the 「错题讲解」 skill.

GET  /api/wrong                 学生：只看本人；教师：看本班
GET  /api/wrong/<wid>           本人 / 本班教师，否则与不存在同构 404
POST /api/wrong/<wid>/explain   调「错题讲解」技能，技能关闭返 503

collect_wrong_question() is the hook app/grader.py calls when a submission is
marked wrong (ai_score < 60) — it lives here so the wrong-question table has a
single writer.
"""
from flask import Blueprint, jsonify

from . import db, retrieval
from .assistant import (NO_EVIDENCE_TEXT, SKILL_PROMPTS, ChatGatewayUnavailable,
                        call_chat, parse_json_block)
from .audit import record_audit
from .auth import get_current_user, require_auth

wrong_bp = Blueprint("wrong", __name__, url_prefix="/api")

SKILL_NAME = "wrong_answer"          # machine key in assistant_skills.skill_name
WRONG_REASON_FALLBACK = "AI 批改未通过"
TOP_K = 4


def _err(message: str, status: int):
    return jsonify({"error": message, "message": message}), status


def _wrong_json(w: dict) -> dict:
    return {
        "id": w["id"],
        "subject": w["subject"],
        "question_text": w["question_text"],
        "student_answer": w["student_answer"],
        "wrong_reason": w["wrong_reason"],
        "explanation": w["explanation"],
        "status": w["status"],
    }


def _may_read(user: dict, w: dict) -> bool:
    """学生只能看本人错题；教师只能看本班错题（不存在与越权同样 404）。"""
    if user["role"] == "teacher":
        return w["class_id"] == user["class_id"]
    return w["student_id"] == user["id"]


# ---------------------------------------------------------------------------
# Write hook for the grader
# ---------------------------------------------------------------------------

def collect_wrong_question(submission: dict, assignment: dict, grade_result: dict) -> int:
    """判错自动归集：question_text 取题干前 200 字，等待学生点「请助手讲解」。"""
    return db.insert_wrong_question(
        student_id=submission["student_id"],
        submission_id=submission["id"],
        class_id=submission["class_id"],
        subject=assignment["subject"],
        question_text=(assignment.get("body") or "")[:200],
        student_answer=submission["answer_text"],
        wrong_reason=(grade_result or {}).get("wrong_reason") or WRONG_REASON_FALLBACK,
    )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

@wrong_bp.route("/wrong", methods=["GET"])
@require_auth
def list_wrong():
    user = get_current_user()
    try:
        if user["role"] == "student":
            items = db.list_wrong_questions_by_student(user["id"])
        else:
            items = db.list_wrong_questions_by_class(user["class_id"])
    except Exception:
        return _err("service unavailable", 503)
    return jsonify({"wrong_questions": [_wrong_json(w) for w in items]})


@wrong_bp.route("/wrong/<int:wid>", methods=["GET"])
@require_auth
def get_wrong(wid: int):
    user = get_current_user()
    try:
        w = db.get_wrong_question(wid)
    except Exception:
        return _err("service unavailable", 503)
    if not w or not _may_read(user, w):
        return _err("wrong question not found", 404)
    return jsonify(_wrong_json(w))


# ---------------------------------------------------------------------------
# 「错题讲解」skill
# ---------------------------------------------------------------------------

def _research(question: str, class_id: int):
    """讲义出处 —— 检索失败只丢引用，不挡住讲解本身。"""
    try:
        return retrieval.search("hybrid", question, class_id, top_k=TOP_K).hits
    except Exception:
        return []


def _context_block(hits) -> str:
    """Same 资料块 format app/qa.py writes, so the mock gateway can parse it."""
    parts = []
    for i, h in enumerate(hits, start=1):
        parts.append(
            f"[{i}] 材料标题：{h.material_title}\n"
            f"切片序号：#{h.chunk_index}\n"
            f"切片正文：\n{h.excerpt}"
        )
    return "以下是相关资料：\n\n" + "\n\n".join(parts)


def _compose_explanation(wrong_reason: str, ai_text: str, hits) -> str:
    """保证三段（spec: explanation 含 错因 + 思路 + 讲义出处）。

    网关按 prompt 回 `{"explanation": "1) 错因… 2) 思路… 3) 讲义出处…"}` 时直接用
    它的正文，绝不再包一层（否则把 JSON 原样塞进正文）。解析不到 JSON（extractive
    mock 网关必然如此）才用网关散文 + 检索到的标题拼出三段。
    讲义原句照录，不做答案剔除 —— 由 prompt 约束模型不直接给答案。
    """
    parsed = parse_json_block(ai_text)
    if parsed is not None:
        body = str(parsed.get("explanation") or "").strip()
        if body:
            return body

    reason = wrong_reason or WRONG_REASON_FALLBACK
    thought = (ai_text or "").strip()
    if not thought or thought == NO_EVIDENCE_TEXT:
        thought = ("先在讲义里找到对应的定义或例题，按步骤重新推导一遍，"
                   "再回头检查出错的那一步。")
    titles = []
    for h in hits:
        if h.material_title not in titles:
            titles.append(h.material_title)
    sources = "、".join(titles) if titles else "本班讲义（本次未检索到具体切片）"
    return (f"1) 错因：{reason}\n\n"
            f"2) 思路：{thought}\n\n"
            f"3) 讲义出处：{sources}")


def _skill_prompt(subject: str, question: str, student_answer: str, wrong_reason: str) -> str:
    return SKILL_PROMPTS[SKILL_NAME].format(
        subject=subject, question=question,
        student_answer=student_answer, wrong_reason=wrong_reason or WRONG_REASON_FALLBACK,
    )


@wrong_bp.route("/wrong/<int:wid>/explain", methods=["POST"])
@require_auth
def explain_wrong(wid: int):
    user = get_current_user()
    try:
        w = db.get_wrong_question(wid)
    except Exception:
        return _err("service unavailable", 503)
    if not w or not _may_read(user, w):
        return _err("wrong question not found", 404)

    try:
        enabled = db.get_assistant_skill_enabled(w["subject"], SKILL_NAME)
    except Exception:
        return _err("service unavailable", 503)
    if not enabled:
        return _err("skill disabled", 503)

    hits = _research(w["question_text"], w["class_id"])
    prompt = _skill_prompt(w["subject"], w["question_text"],
                           w["student_answer"], w["wrong_reason"])
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": f"题目：{w['question_text']}\n\n"
                                    f"学生作答：{w['student_answer']}\n\n"
                                    f"{_context_block(hits)}"},
    ]

    try:
        ai_text = call_chat(messages, timeout=30)
    except ChatGatewayUnavailable:
        return _err("service unavailable", 503)

    explanation = _compose_explanation(w["wrong_reason"], ai_text, hits)
    try:
        db.set_wrong_explanation(wid, explanation)
    except Exception:
        return _err("service unavailable", 503)

    record_audit("wrong_explain", target_id=wid)
    return jsonify({"explanation": explanation, "status": "explained"})