"""POST /api/agents

① 教师创建（含 skill_ids / material_ids）→ 201，DB 核对主表 + 两张绑定表
② 学生调用 → 403
③ 绑定他班 material_id → 400
④ 同名重复 → 400
"""
import uuid

import pytest
import requests


def _first_id(db, table, cid):
    with db.get_conn() as conn:
        row = db._one(conn, f"SELECT id FROM {table} WHERE class_id = %s ORDER BY id LIMIT 1",
                      (cid,))
    return int(row["id"]) if row else None


def test_create_agent_teacher_and_db(base_url, teacher_headers, db, class_id_of):
    cid = class_id_of("teacher_a")
    skill_id = _first_id(db, "skills", cid)
    material_id = _first_id(db, "materials", cid)
    if skill_id is None or material_id is None:
        pytest.skip("缺少本班技能或材料种子，无法核对绑定")

    name = "apitest-agent-" + uuid.uuid4().hex[:8]
    payload = {"name": name, "prompt": "apitest 角色提示词", "subject": "math",
               "skill_ids": [skill_id], "material_ids": [material_id]}
    r = requests.post(f"{base_url}/api/agents", headers=teacher_headers,
                      json=payload, timeout=15)
    assert r.status_code == 201, r.text
    aid = r.json()["id"]
    try:
        with db.get_conn() as conn:
            row = db._one(conn, "SELECT class_id, prompt FROM tutor_agents WHERE id = %s",
                          (aid,))
            assert row is not None
            assert int(row["class_id"]) == cid, row
            s = db._rows(conn, "SELECT skill_id FROM tutor_agent_skills WHERE agent_id = %s",
                         (aid,))
            m = db._rows(conn, "SELECT material_id FROM tutor_agent_materials WHERE agent_id = %s",
                         (aid,))
            assert {int(x["skill_id"]) for x in s} == {skill_id}, s
            assert {int(x["material_id"]) for x in m} == {material_id}, m
    finally:
        requests.delete(f"{base_url}/api/agents/{aid}", headers=teacher_headers, timeout=15)


def test_create_agent_student_forbidden(base_url, student_headers):
    r = requests.post(f"{base_url}/api/agents", headers=student_headers,
                      json={"name": "should-fail"}, timeout=15)
    assert r.status_code == 403, r.text


def test_create_agent_cross_class_material_400(base_url, teacher_headers, db, class_id_of):
    other_mid = _first_id(db, "materials", class_id_of("student_b1"))
    if other_mid is None:
        pytest.skip("他班没有材料可用来验证跨班绑定")
    r = requests.post(f"{base_url}/api/agents", headers=teacher_headers,
                      json={"name": "apitest-cross-" + uuid.uuid4().hex[:6],
                            "material_ids": [other_mid]}, timeout=15)
    assert r.status_code == 400, r.text


def test_create_agent_duplicate_name_400(base_url, teacher_headers):
    name = "apitest-dup-" + uuid.uuid4().hex[:8]
    first = requests.post(f"{base_url}/api/agents", headers=teacher_headers,
                          json={"name": name}, timeout=15)
    assert first.status_code == 201, first.text
    aid = first.json()["id"]
    try:
        dup = requests.post(f"{base_url}/api/agents", headers=teacher_headers,
                            json={"name": name}, timeout=15)
        assert dup.status_code == 400, dup.text
    finally:
        requests.delete(f"{base_url}/api/agents/{aid}", headers=teacher_headers, timeout=15)
