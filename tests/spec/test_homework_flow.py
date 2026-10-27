"""作业流程的 21 个 Scenario 端到端测试（spec: homework-flow）。

运行：`make check`（在 app 容器内 pytest tests/ -v）。
每个用例都是一次真实的 HTTP 往返，打完整的 Flask 路由 + JWT 装饰器 + MySQL。

网关默认被 conftest 的 `stub_gateways` 打桩（批改 85 分 / 讲解三段），
所以本套件不依赖外网；真实网关的验证在 `make eval` 与 tests/acceptance.py。

编号与 spec.md 的 Scenario 顺序一一对应。
"""
import json
import time
from datetime import datetime, timedelta, timezone

from app import db, grader
from app.assistant import ChatGatewayUnavailable


def _fields(row):
    """audit_log.fields 是 JSON 列 —— 取回后可能仍是字符串。"""
    raw = row.get("fields")
    if not raw:
        return {}
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "replace")
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return raw


def _future_iso(days=7):
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


# ===========================================================================
# Requirement: 教师 SHALL 可发布本班作业
# ===========================================================================

def test_scenario_1_teacher_creates_assignment(client, auth, teacher_a_token,
                                               audit_rows, user_id, class_id):
    """正常路径：201 + 落库 + 审计。deadline 带 +08:00 偏移也要能解析。"""
    r = client.post("/api/assignments", json={
        "title": "函数单调性练习",
        "body": "判断 f(x)=x^2-4x 在 (2,∞) 上的单调性",
        "subject": "math",
        "deadline": "2026-10-15T23:59:00+08:00",
    }, headers=auth(teacher_a_token))

    assert r.status_code == 201, r.get_data(as_text=True)
    aid = r.get_json()["assignment_id"]

    row = db.get_assignment(aid, class_id("teacher_a"))
    assert row is not None
    assert row["teacher_id"] == user_id("teacher_a")
    assert row["class_id"] == class_id("teacher_a")
    assert row["status"] == "published"

    assert audit_rows("create_assignment", aid), "缺 create_assignment 审计行"


def test_scenario_2_missing_class_id_derived_from_token(client, auth,
                                                        teacher_a_token, class_id):
    """与 spec 第 24-28 行有意的分歧：规约写「缺 class_id → 400」，但本轮决策是
    「不要求客户端传 class_id，一律取自 token」。因此断言：不传 class_id 仍然
    201，且落库的 class_id 来自 token 而非请求体。"""
    r = client.post("/api/assignments", json={
        "title": "不传 class_id",
        "body": "题干",
        "subject": "math",
        "deadline": _future_iso(3),
    }, headers=auth(teacher_a_token))

    assert r.status_code == 201, r.get_data(as_text=True)
    aid = r.get_json()["assignment_id"]
    assert db.get_assignment(aid, class_id("teacher_a")) is not None


def test_scenario_3_student_cannot_create_assignment(client, auth,
                                                     student_a1_token, audit_rows):
    r = client.post("/api/assignments", json={
        "title": "x", "body": "y", "subject": "math", "deadline": _future_iso(3),
    }, headers=auth(student_a1_token))

    assert r.status_code == 403
    assert "仅教师可发布作业" in json.dumps(r.get_json(), ensure_ascii=False)

    rows = audit_rows("create_assignment_forbidden")
    assert rows, "越权发布没有记审计"
    assert _fields(rows[0]).get("level") == "warning"


# ===========================================================================
# Requirement: 学生 SHALL 可提交本班作业
# ===========================================================================

def test_scenario_4_student_submits(client, auth, teacher_a_token,
                                    student_a1_token, publish, submit,
                                    audit_rows, user_id):
    aid = publish(teacher_a_token)
    r = submit(student_a1_token, aid, answer="x=2 或 x=3")

    assert r.status_code == 201, r.get_data(as_text=True)
    body = r.get_json()
    assert body["status"] == "pending"
    sid = body["submission_id"]

    row = db.get_submission(sid)
    assert row["student_id"] == user_id("student_a1")
    assert row["status"] in ("pending", "processing", "done")
    assert audit_rows("submit_homework", sid), "缺 submit_homework 审计行"


def test_scenario_5_student_cannot_submit_other_class(client, auth,
                                                      teacher_a_token,
                                                      student_b1_token,
                                                      publish, submit, user_id):
    aid = publish(teacher_a_token)  # A 班的作业
    r = submit(student_b1_token, aid, answer="B 班学生想交 A 班作业")

    assert r.status_code == 404
    assert r.get_json().get("message") == "assignment not found"
    assert db.find_submission(aid, user_id("student_b1")) is None


def test_scenario_6_duplicate_submission(client, auth, teacher_a_token,
                                         student_a1_token, publish, submit):
    aid = publish(teacher_a_token)
    assert submit(student_a1_token, aid).status_code == 201

    r = submit(student_a1_token, aid, answer="再交一次")
    assert r.status_code == 409
    assert r.get_json().get("message") == "already submitted"


def test_scenario_7_submission_after_deadline(client, auth, teacher_a_token,
                                              student_a1_token, publish, submit):
    aid = publish(teacher_a_token, days=-1)  # 截止时间已过
    r = submit(student_a1_token, aid, answer="迟交")

    assert r.status_code == 410
    assert r.get_json().get("message") == "deadline passed"


def test_scenario_8_empty_answer(client, auth, teacher_a_token, student_a1_token,
                                 publish):
    aid = publish(teacher_a_token)
    r = client.post("/api/submissions", json={"assignment_id": aid, "answer_text": ""},
                    headers=auth(student_a1_token))

    assert r.status_code == 400
    assert r.get_json().get("message") == "answer_text required"


# ===========================================================================
# Requirement: 助手 SHALL 在异步批改后状态变更为 done
# ===========================================================================

def test_scenario_9_async_grading_succeeds(client, auth, teacher_a_token,
                                           student_a1_token, publish, submit,
                                           wait_status, audit_rows):
    aid = publish(teacher_a_token)
    sid = submit(student_a1_token, aid, answer="x=2 或 x=3").get_json()["submission_id"]

    final = wait_status(student_a1_token, sid, "done")
    grade = final["grade"]
    assert grade is not None
    assert 0 <= grade["ai_score"] <= 100
    assert grade["ai_comment"]
    assert isinstance(grade["ai_citations"], list)
    assert audit_rows("ai_grade_done", sid), "缺 ai_grade_done 审计行"


def test_scenario_10_grading_timeout_marks_failed(client, auth, teacher_a_token,
                                                  student_a1_token, publish,
                                                  submit, wait_status,
                                                  monkeypatch, audit_rows):
    def _timeout(messages, timeout=30):
        raise ChatGatewayUnavailable("timeout")

    monkeypatch.setattr(grader, "call_chat", _timeout)

    aid = publish(teacher_a_token)
    sid = submit(student_a1_token, aid, answer="网关会超时").get_json()["submission_id"]

    final = wait_status(student_a1_token, sid, "failed")
    assert final["error_msg"] == "grading failed: timeout"
    assert db.get_grade(sid) is None, "批改失败不得写 grades"
    assert audit_rows("ai_grade_failed", sid), "缺 ai_grade_failed 审计行"


def test_scenario_11_low_score_collects_wrong(client, auth, teacher_a_token,
                                              student_a1_token, graded_wrong,
                                              user_id):
    """判错（ai_score < 60）→ 自动归集错题，等待学生点「请助手讲解」。"""
    row, sid = graded_wrong(teacher_a_token, student_a1_token, score=55)

    assert row["student_id"] == user_id("student_a1")
    assert row["submission_id"] == sid
    assert len(row["question_text"]) <= 200
    assert row["status"] == "pending"


# ===========================================================================
# Requirement: 教师 SHALL 可改分覆盖助手终评
# ===========================================================================

def test_scenario_12_teacher_overrides_grade(client, auth, teacher_a_token,
                                             student_a1_token, publish, submit,
                                             wait_status, audit_rows, user_id):
    aid = publish(teacher_a_token)
    sid = submit(student_a1_token, aid).get_json()["submission_id"]
    wait_status(student_a1_token, sid, "done")

    r = client.put(f"/api/submissions/{sid}/grade",
                   json={"final_score": 77, "final_comment": "思路对，计算粗心"},
                   headers=auth(teacher_a_token))
    assert r.status_code == 200, r.get_data(as_text=True)

    grade = db.get_grade(sid)
    assert grade["final_score"] == 77
    assert grade["final_comment"] == "思路对，计算粗心"
    assert grade["graded_by"] == user_id("teacher_a")

    rows = audit_rows("teacher_grade", sid)
    assert rows, "缺 teacher_grade 审计行"
    fields = _fields(rows[0])
    assert fields.get("final_score") == 77
    assert "ai_score" in fields  # 改分前的 AI 分数也在审计里


def test_scenario_13_teacher_cannot_grade_other_class(client, auth,
                                                      teacher_a_token,
                                                      teacher_b_token,
                                                      student_b1_token,
                                                      publish, submit, wait_status):
    # B 班的一条已完成批改的提交
    aid_b = publish(teacher_b_token)
    sid_b = submit(student_b1_token, aid_b).get_json()["submission_id"]
    wait_status(student_b1_token, sid_b, "done")

    r = client.put(f"/api/submissions/{sid_b}/grade",
                   json={"final_score": 90, "final_comment": "越权改分"},
                   headers=auth(teacher_a_token))
    assert r.status_code == 404
    assert r.get_json().get("message") == "submission not found"


def test_scenario_14_student_cannot_grade(client, auth, teacher_a_token,
                                          student_a1_token, publish, submit):
    aid = publish(teacher_a_token)
    sid = submit(student_a1_token, aid).get_json()["submission_id"]

    r = client.put(f"/api/submissions/{sid}/grade",
                   json={"final_score": 100, "final_comment": "学生改分"},
                   headers=auth(student_a1_token))
    assert r.status_code == 403
    assert "仅教师可改分" in json.dumps(r.get_json(), ensure_ascii=False)


# ===========================================================================
# Requirement: 错题讲解技能 SHALL 按错因说明，不直接给答案
# ===========================================================================

def test_scenario_15_explain_succeeds(client, auth, teacher_a_token,
                                      student_a1_token, graded_wrong, audit_rows):
    row, _sid = graded_wrong(teacher_a_token, student_a1_token)
    wid = row["id"]

    r = client.post(f"/api/wrong/{wid}/explain", headers=auth(student_a1_token))
    assert r.status_code == 200, r.get_data(as_text=True)

    text = r.get_json()["explanation"]
    assert "错因" in text and "思路" in text and "讲义出处" in text

    assert db.get_wrong_question(wid)["status"] == "explained"
    assert audit_rows("wrong_explain", wid), "缺 wrong_explain 审计行"


def test_scenario_16_explain_skill_disabled(client, auth, teacher_a_token,
                                            student_a1_token, graded_wrong):
    row, _sid = graded_wrong(teacher_a_token, student_a1_token, score=55)
    wid = row["id"]

    db.upsert_assistant_skill(row["subject"], "wrong_answer", False)
    try:
        r = client.post(f"/api/wrong/{wid}/explain", headers=auth(student_a1_token))
        assert r.status_code == 503
        assert r.get_json().get("message") == "skill disabled"
    finally:
        db.upsert_assistant_skill(row["subject"], "wrong_answer", True)


def test_scenario_17_explain_other_class(client, auth, teacher_b_token,
                                         student_b1_token, student_a1_token,
                                         graded_wrong):
    row_b, _sid = graded_wrong(teacher_b_token, student_b1_token, score=55)

    r = client.post(f"/api/wrong/{row_b['id']}/explain",
                    headers=auth(student_a1_token))
    assert r.status_code == 404
    assert r.get_json().get("message") == "wrong question not found"


# ===========================================================================
# Requirement: 班级隔离 MUST 严格生效
# ===========================================================================

def test_scenario_18_teacher_cannot_see_other_class(client, auth, teacher_a_token,
                                                    class_id):
    other = 2 if class_id("teacher_a") != 2 else 1
    r = client.get(f"/api/assignments?class_id={other}", headers=auth(teacher_a_token))

    assert r.status_code == 200
    assert r.get_json()["assignments"] == []


def test_scenario_19_student_cannot_see_other_class(client, auth, student_a1_token,
                                                    class_id):
    other = 2 if class_id("student_a1") != 2 else 1
    r = client.get(f"/api/assignments?class_id={other}", headers=auth(student_a1_token))

    assert r.status_code == 200
    assert r.get_json()["assignments"] == []


def test_scenario_20_student_cannot_see_other_wrong(client, auth, teacher_b_token,
                                                    student_b1_token,
                                                    student_a1_token, graded_wrong):
    row_b, _sid = graded_wrong(teacher_b_token, student_b1_token, score=55)

    r = client.get(f"/api/wrong/{row_b['id']}", headers=auth(student_a1_token))
    assert r.status_code == 404


# ===========================================================================
# Requirement: 异步批改 SHALL NOT 阻塞提交
# ===========================================================================

def test_scenario_21_submit_returns_quickly(client, auth, teacher_a_token,
                                            student_a1_token, publish):
    # 预热：先跑一次路由（过期作业 → 410，不写库），避免首调用开销混进计时
    expired = publish(teacher_a_token, days=-1)
    client.post("/api/submissions", json={"assignment_id": expired, "answer_text": "warm"},
                headers=auth(student_a1_token))

    aid = publish(teacher_a_token)
    t0 = time.perf_counter()
    r = client.post("/api/submissions", json={"assignment_id": aid, "answer_text": "x=2"},
                    headers=auth(student_a1_token))
    elapsed_ms = (time.perf_counter() - t0) * 1000

    assert r.status_code == 201, r.get_data(as_text=True)
    # 批改在后台线程里；POST 不等待它
    assert r.get_json()["status"] == "pending"
    assert elapsed_ms < 200, f"POST /api/submissions 耗时 {elapsed_ms:.0f}ms ≥ 200ms"