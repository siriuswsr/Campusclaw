"""Lesson 6 — 异步批改：POST /api/submissions 起一个守护线程，线程里跑网关批改。

Pipeline (spec: 异步批改 SHALL NOT 阻塞提交):
  pending → processing → done          (批改成功，写 grades；判错则归集错题)
  pending → processing → failed        (网关超时/不可用，error_msg 说明原因)

Scoring: the gateway is ASKED for JSON (SKILL_PROMPTS['homework_grade']). When the
reply is not parseable JSON — which is always the case with this repo's extractive
mock gateway — we fall back to a deterministic heuristic: the bigram overlap between
the student's answer and the retrieved 讲义 chunks, mapped to 0-100. Same input,
same score; no invented similarity numbers are shown to anyone.

TODO(week-08): replace the thread with Celery + Redis.
"""
import re
import threading
from datetime import datetime

from . import db, retrieval
from .assistant import (NO_EVIDENCE_TEXT, SKILL_PROMPTS, ChatGatewayUnavailable,
                        call_chat, parse_json_block)
from .audit import record_audit
from .wrong import collect_wrong_question

_GRADING_JOBS = {}

SKILL_NAME = "homework_grade"
PASS_SCORE = 60          # < 60 → 归集错题
GATEWAY_TIMEOUT = 30
TOP_K = 4

_WORD_RE = re.compile(r"[a-z0-9]{2,}")


# ---------------------------------------------------------------------------
# Thread entry
# ---------------------------------------------------------------------------

def grade_async(submission_id: int, assignment_id: int = None) -> None:
    """Fire-and-forget: the caller (POST /api/submissions) returns immediately.

    assignment_id is accepted for parity with the spec's call shape
    (grade_async(s.id, a.id)) — the thread re-reads it from the submission anyway.
    """
    t = threading.Thread(target=_grade_sync, args=(submission_id,), daemon=True)
    _GRADING_JOBS[submission_id] = t
    t.start()


def _grade_sync(submission_id: int) -> None:
    from . import create_app

    app = create_app()
    with app.app_context():
        try:
            sub = db.get_submission(submission_id)
            if not sub:
                return
            db.mark_submission(submission_id, "processing")

            assignment = db.get_assignment(sub["assignment_id"], sub["class_id"])
            if not assignment:
                # 作业被删了（本 change 无删除接口）——不再编造分数，直接失败
                db.mark_submission(submission_id, "failed", "grading failed: assignment missing")
                record_audit("ai_grade_failed", target_id=submission_id,
                             error="assignment missing")
                return

            hits = _research(assignment["body"], sub["class_id"])
            result = _call_grader(assignment, sub, hits)
        except ChatGatewayUnavailable as e:
            _fail(submission_id, f"grading failed: {e}")
            return
        except Exception as e:
            _fail(submission_id, f"grading failed: {e}")
            return

        try:
            db.insert_ai_grade(submission_id, result["score"], result["comment"],
                               result["citations"])
            db.mark_submission(submission_id, "done", None, datetime.utcnow())
        except Exception as e:
            _fail(submission_id, f"grading failed: {e}")
            return

        record_audit("ai_grade_done", target_id=submission_id, score=result["score"])

        # 判错自动归集错题（spec: ai_score < 60 → wrong_questions 新增一行）
        if result["score"] < PASS_SCORE:
            try:
                collect_wrong_question(sub, assignment, result)
            except Exception as e:
                # 归集失败不该把已完成的批改打回 failed —— 分数已经落库了
                record_audit("ai_grade_wrong_collect_failed", target_id=submission_id,
                             error=str(e))


def _fail(submission_id: int, message: str) -> None:
    try:
        db.mark_submission(submission_id, "failed", message)
    except Exception:
        pass
    record_audit("ai_grade_failed", target_id=submission_id, error=message)


# ---------------------------------------------------------------------------
# Gateway + scoring
# ---------------------------------------------------------------------------

def _research(question: str, class_id: int):
    """讲义出处；检索不可用时只丢引用，批改照跑（不再是 503 的阻塞点）。"""
    try:
        return retrieval.search("hybrid", question, class_id, top_k=TOP_K).hits
    except Exception:
        return []


def _call_grader(assignment: dict, sub: dict, hits) -> dict:
    prompt = SKILL_PROMPTS[SKILL_NAME].format(
        subject=assignment["subject"],
        question=assignment["body"],
        student_answer=sub["answer_text"],
    )
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": _grading_user_message(assignment, sub, hits)},
    ]
    ai_text = call_chat(messages, timeout=GATEWAY_TIMEOUT)
    return _score(ai_text, sub["answer_text"], hits)


def _grading_user_message(assignment: dict, sub: dict, hits) -> str:
    """Same 资料块 format app/qa.py writes (and mock_gateway.parse_chunks reads)."""
    parts = [f"题目：{assignment['body']}", "", f"学生作答：{sub['answer_text']}",
             "", "以下是相关资料：", ""]
    for i, h in enumerate(hits, start=1):
        parts.append(
            f"[{i}] 材料标题：{h.material_title}\n"
            f"切片序号：#{h.chunk_index}\n"
            f"切片正文：\n{h.excerpt}"
        )
    return "\n".join(parts)


def _hit_titles(hits) -> list:
    titles = []
    for h in hits:
        if h.material_title not in titles:
            titles.append(h.material_title)
    return titles


def _score(ai_text: str, answer_text: str, hits) -> dict:
    """JSON 优先；解析不到（mock 网关必然如此）走确定性启发式。"""
    titles = _hit_titles(hits)
    parsed = parse_json_block(ai_text)
    if parsed is not None and isinstance(parsed.get("score"), (int, float)):
        score = max(0, min(100, int(parsed["score"])))
        comment = str(parsed.get("comment") or "").strip() or ai_text.strip()
        wrong_reason = str(parsed.get("wrong_reason") or "").strip()
        citations = parsed.get("citations") or titles
        if not isinstance(citations, list):
            citations = titles
        return {"score": score, "comment": comment,
                "wrong_reason": wrong_reason, "citations": citations}

    score = _heuristic_score(answer_text, hits)
    comment = (ai_text or "").strip() or NO_EVIDENCE_TEXT
    return {
        "score": score,
        "comment": comment,
        "wrong_reason": "" if score >= PASS_SCORE else "学生作答与本班讲义要点的重合度过低",
        "citations": titles,
    }


def _bigrams(text: str) -> set:
    """CJK bigrams + ascii words — mirrors mock_gateway.bigram_set so the fallback
    measures the same overlap the mock gateway used to pick its quotes."""
    text = (text or "").lower()
    out = set()
    for i in range(len(text) - 1):
        a, b = text[i], text[i + 1]
        if "\u4e00" <= a <= "\u9fff" and "\u4e00" <= b <= "\u9fff":
            out.add(a + b)
    out.update(_WORD_RE.findall(text))
    return out


def _heuristic_score(answer_text: str, hits) -> int:
    """学生作答被本班讲义覆盖的比例，映射到 0-100。无检索结果即 0 分。"""
    answer = _bigrams(answer_text)
    if not answer:
        return 0
    reference = set()
    for h in hits:
        reference |= _bigrams(h.excerpt)
    if not reference:
        return 0
    return max(0, min(100, round(len(answer & reference) / len(answer) * 100)))