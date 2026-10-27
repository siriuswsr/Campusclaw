"""跑评测：遍历 eval/questions.yaml，逐题调真实课程网关，按 eval/grading.md 判分。

在 app 容器内执行（`make eval`）：网关密钥只在容器的环境变量里，
用 `app.assistant.call_chat` 走与线上完全一致的调用路径。

与线上批改一致：批改分数由 `app.grader._score` 解析（JSON 优先、启发式兜底），
这样评测量到的就是产品真实使用的判分逻辑。
"""
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.assistant import SKILL_PROMPTS, call_chat, parse_json_block  # noqa: E402
from app.grader import _score                                          # noqa: E402

PASS_RATIO = 0.8
GATEWAY_TIMEOUT = 60
RETRIES = 3          # 课程网关偶发 RemoteDisconnected，重试几次再判失败
RETRY_BACKOFF = 3    # 秒


def _call_with_retry(messages):
    """调网关；瞬时连接类错误退避重试，其它异常直接抛出。"""
    last = None
    for attempt in range(RETRIES):
        try:
            return call_chat(messages, timeout=GATEWAY_TIMEOUT)
        except Exception as e:                      # noqa: BLE001
            last = e
            if attempt < RETRIES - 1:
                time.sleep(RETRY_BACKOFF)
    raise last


def _grade(question, student_answer):
    """POST 一次「作业批改」技能 → {score, comment, wrong_reason, citations}。"""
    prompt = SKILL_PROMPTS["homework_grade"].format(
        subject="math", question=question, student_answer=student_answer)
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": f"题目：{question}\n\n学生作答：{student_answer}"},
    ]
    text = _call_with_retry(messages)
    return _score(text, student_answer, hits=[])


def _explain(question, student_answer, wrong_reason):
    """POST 一次「错题讲解」技能 → explanation 正文。"""
    prompt = SKILL_PROMPTS["wrong_answer"].format(
        subject="math", question=question, student_answer=student_answer,
        wrong_reason=wrong_reason)
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": f"题目：{question}\n\n学生作答：{student_answer}"},
    ]
    text = _call_with_retry(messages)
    parsed = parse_json_block(text)
    if parsed and parsed.get("explanation"):
        return str(parsed["explanation"])
    return text


def _judge_grade(q, comment, score):
    lo, hi = q["expect_score_range"]
    problems = []
    if not (lo <= score <= hi):
        problems.append(f"score={score} 不在 [{lo}, {hi}]")
    for kw in q.get("expect_includes", []):
        if kw not in comment:
            problems.append(f"评语缺关键词 {kw!r}")
    for kw in q.get("expect_excludes", []):
        if kw in comment:
            problems.append(f"评语泄漏关键词 {kw!r}")
    return problems


def _judge_explain(q, text):
    problems = []
    for kw in q.get("expected_response_includes", []):
        if kw not in text:
            problems.append(f"缺关键词 {kw!r}")
    for kw in q.get("expected_response_excludes", []):
        if kw in text:
            problems.append(f"泄漏关键词 {kw!r}")
    return problems


def main():
    questions = yaml.safe_load(
        (ROOT / "eval" / "questions.yaml").read_text(encoding="utf-8"))
    passed = 0
    total = len(questions)

    print("=" * 72)
    for q in questions:
        qid = q["id"]
        raw = ""
        try:
            if qid.startswith("grade"):
                result = _grade(q["setup"], q["student_answer"])
                raw = result.get("comment") or ""
                problems = _judge_grade(q, raw, result["score"])
                detail = f"score={result['score']}"
            elif qid.startswith("wrong"):
                raw = _explain(q["setup"], q.get("student_answer", ""),
                               q.get("wrong_reason", ""))
                problems = _judge_explain(q, raw)
                detail = f"len={len(raw)}"
            else:
                problems, detail = [f"未知 id 前缀: {qid}"], "-"
        except Exception as e:                      # 网关不可用等
            problems, detail = [f"调用失败: {e}"], "-"

        ok = not problems
        passed += ok
        print(f"{'PASS' if ok else 'FAIL'} {qid}  ({detail})")
        if not ok:
            for p in problems:
                print(f"      - {p}")
            if raw:
                print(f"      实际输出: {raw[:200]}")

    ratio = passed / total
    print("=" * 72)
    print(f"core 集：{passed}/{total} 通过（{ratio * 100:.0f}%）"
          f"，通过线 {PASS_RATIO * 100:.0f}%")
    return 0 if ratio >= PASS_RATIO else 1


if __name__ == "__main__":
    sys.exit(main())