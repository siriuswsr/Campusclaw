"""POST /api/skills（text/plain 技能文件全文）

① 合法文件 → 201，DB 核对 skills 行归属本班
② 缺必填字段（启用）→ 400
③ 非法工具名 web_search → 400
④ 同名重复 → 400（班内唯一）
⑤ 学生调用 → 403
"""
import uuid

import requests


def _skill_text(name, trigger="测试,验证", subject="数学",
                tool="knowledge_search", enabled="是", body="用于 apitest 的技能正文。"):
    lines = [f"名称：{name}", f"适用条件：{trigger}"]
    if subject is not None:
        lines.append(f"资料：{subject}")
    if tool is not None:
        lines.append(f"工具：{tool}")
    lines.append(f"启用：{enabled}")
    return "\n".join(lines) + "\n\n" + body


def _post(base_url, headers, text):
    h = dict(headers)
    h["Content-Type"] = "text/plain; charset=utf-8"
    return requests.post(f"{base_url}/api/skills", headers=h,
                         data=text.encode("utf-8"), timeout=15)


def test_upload_skill_ok_and_db(base_url, teacher_headers, db, class_id_of):
    cid = class_id_of("teacher_a")
    name = "apitest技能" + uuid.uuid4().hex[:6]
    r = _post(base_url, teacher_headers, _skill_text(name))
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    try:
        with db.get_conn() as conn:
            row = db._one(conn,
                          "SELECT class_id, name, subject_text FROM skills WHERE id = %s",
                          (sid,))
            assert row is not None
            assert int(row["class_id"]) == cid, row
            assert row["name"] == name, row
            assert row["subject_text"] == "数学", row
    finally:
        requests.delete(f"{base_url}/api/skills/{sid}", headers=teacher_headers, timeout=15)


def test_upload_skill_missing_field_400(base_url, teacher_headers):
    text = "名称：缺启用" + uuid.uuid4().hex[:6] + "\n适用条件：测试\n\n正文。"
    r = _post(base_url, teacher_headers, text)
    assert r.status_code == 400, r.text


def test_upload_skill_illegal_tool_400(base_url, teacher_headers):
    r = _post(base_url, teacher_headers,
              _skill_text("非法工具" + uuid.uuid4().hex[:6], tool="web_search"))
    assert r.status_code == 400, r.text


def test_upload_skill_duplicate_name_400(base_url, teacher_headers):
    name = "重名技能" + uuid.uuid4().hex[:6]
    first = _post(base_url, teacher_headers, _skill_text(name))
    assert first.status_code == 201, first.text
    sid = first.json()["id"]
    try:
        dup = _post(base_url, teacher_headers, _skill_text(name))
        assert dup.status_code == 400, dup.text
    finally:
        requests.delete(f"{base_url}/api/skills/{sid}", headers=teacher_headers, timeout=15)


def test_upload_skill_student_forbidden(base_url, student_headers):
    r = _post(base_url, student_headers, _skill_text("学生上传" + uuid.uuid4().hex[:6]))
    assert r.status_code == 403, r.text
