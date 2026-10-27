"""GET /api/agents

① 教师看到本班全部智能体（含停用），数量与 DB 一致
② 学生只看到 enabled=true 的，数量与 DB 一致
③ 学生可见的智能体 id 全部属于本班（跨班不外泄）
"""
import requests


def test_agents_teacher_sees_all(base_url, teacher_headers, db, class_id_of):
    cid = class_id_of("teacher_a")
    r = requests.get(f"{base_url}/api/agents", headers=teacher_headers, timeout=15)
    assert r.status_code == 200, r.text
    agents = r.json()["agents"]
    with db.get_conn() as conn:
        n = db._one(conn, "SELECT COUNT(*) AS c FROM tutor_agents WHERE class_id = %s",
                    (cid,))["c"]
    assert len(agents) == int(n), (len(agents), n)


def test_agents_student_sees_enabled_only(base_url, student_headers, db, class_id_of):
    cid = class_id_of("student_a1")
    r = requests.get(f"{base_url}/api/agents", headers=student_headers, timeout=15)
    assert r.status_code == 200, r.text
    agents = r.json()["agents"]
    with db.get_conn() as conn:
        n = db._one(conn,
                    "SELECT COUNT(*) AS c FROM tutor_agents WHERE class_id = %s AND enabled = 1",
                    (cid,))["c"]
        class_agent_ids = {int(x["id"]) for x in
                           db._rows(conn, "SELECT id FROM tutor_agents WHERE class_id = %s",
                                    (cid,))}
    assert len(agents) == int(n), (len(agents), n)
    assert all(a["enabled"] for a in agents), agents
    assert {a["id"] for a in agents} <= class_agent_ids, "学生看到了本班之外的智能体"


def test_agents_other_class_isolated(base_url, other_headers, db, class_id_of):
    cid_a = class_id_of("teacher_a")
    with db.get_conn() as conn:
        class_a_agents = {int(x["id"]) for x in
                          db._rows(conn, "SELECT id FROM tutor_agents WHERE class_id = %s",
                                   (cid_a,))}
    r = requests.get(f"{base_url}/api/agents", headers=other_headers, timeout=15)
    assert r.status_code == 200, r.text
    assert not ({a["id"] for a in r.json()["agents"]} & class_a_agents), "B 班看到了 A 班智能体"
