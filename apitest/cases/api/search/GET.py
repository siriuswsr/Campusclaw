"""GET /api/search

① 空 q → 400
② 有 q → 200，hits 结构完整（chunk_id/material_id/material_title/excerpt/score）
③ ?subject=数学 时，命中材料的 subject 必须就是 数学（DB 核对，过滤生效）
④ 命中材料的 class_id 必须等于调用者班级（跨班材料不外泄）

③ 用**本用例临时上传**的一篇 subject=数学 讲义来核对 —— 种子库里恰好没有
「既带 subject 又有 ready 切片」的材料，若不新建材料，「全部命中都满足
subject==数学」在零命中时会空跑通过（vacuous pass），验证不到过滤本身。
"""
import os
import uuid

import requests


def _upload(base_url, teacher_headers, title, subject, body):
    r = requests.post(f"{base_url}/materials/api/upload", headers=teacher_headers,
                      files={"file": (title + ".md", body.encode("utf-8"), "text/markdown")},
                      data={"title": title, "subject": subject, "strategy": "auto"},
                      timeout=60)
    assert r.status_code == 201, (r.status_code, r.text)
    return r.json()["material_id"]


def _drop_material(db, mid):
    with db.get_conn() as conn:
        row = db._one(conn, "SELECT file_path FROM materials WHERE id = %s", (mid,))
        db._exec(conn, "DELETE FROM knowledge_chunks WHERE material_id = %s", (mid,))
        db._exec(conn, "DELETE FROM knowledge_entries WHERE material_id = %s", (mid,))
        db._exec(conn, "DELETE FROM materials WHERE id = %s", (mid,))
    if row and row.get("file_path"):
        try:
            os.remove(row["file_path"])
        except OSError:
            pass


def test_search_requires_query(base_url, teacher_headers):
    r = requests.get(f"{base_url}/api/search", headers=teacher_headers, timeout=15)
    assert r.status_code == 400, r.text


def test_search_returns_hits(base_url, teacher_headers):
    # 默认 mode=hybrid 需要向量服务（mock 嵌入网关未起时按 spec 返回 503），
    # 这里只核验收窄与结构，用 keyword 模式（纯 MySQL FULLTEXT）即可。
    r = requests.get(f"{base_url}/api/search", headers=teacher_headers,
                     params={"q": "分数", "mode": "keyword"}, timeout=15)
    assert r.status_code == 200, r.text
    data = r.json()
    assert isinstance(data.get("hits"), list), data
    for h in data["hits"]:
        for key in ("chunk_id", "material_id", "material_title", "excerpt", "score"):
            assert key in h, h


def test_search_subject_filter_takes_effect(base_url, teacher_headers, db, class_id_of):
    cid = class_id_of("teacher_a")
    token = "subjtest" + uuid.uuid4().hex[:8]
    mid = None
    try:
        # 造一篇 subject=数学、含独特 token 的讲义，保证命中非空
        mid = _upload(base_url, teacher_headers, "apitest-" + token, "数学",
                      f"数学自学笔记\n\n分数通分与约分要点 {token}。\n"
                      f"把异分母分数化成同分母分数 {token}。\n")

        r = requests.get(f"{base_url}/api/search", headers=teacher_headers,
                         params={"q": token, "subject": "数学", "mode": "keyword",
                                 "top_k": 20}, timeout=15)
        assert r.status_code == 200, r.text
        hits = r.json()["hits"]
        assert hits, "subject=数学 命中为空 —— 过滤无法体现（非空跑）"
        assert any(h["material_id"] == mid for h in hits), ("未命中刚上传的数学材料", hits)

        with db.get_conn() as conn:
            for h in hits:
                row = db._one(conn,
                              "SELECT class_id, subject FROM materials WHERE id = %s",
                              (h["material_id"],))
                assert row is not None, h
                assert int(row["class_id"]) == cid, ("跨班材料外泄", h, row)
                assert row["subject"] == "数学", ("subject 过滤未生效", h, row)
    finally:
        if mid:
            _drop_material(db, mid)


def test_search_subject_excludes_other_subject(base_url, teacher_headers, db):
    # 同 unique token 打在两篇材料上（数学/语文），带 subject=数学 时应排除语文那篇。
    token = "excl" + uuid.uuid4().hex[:8]
    math_mid = wen_mid = None
    try:
        math_mid = _upload(base_url, teacher_headers, "apitest-math-" + token, "数学",
                           f"数学笔记 {token} 分数通分。\n")
        wen_mid = _upload(base_url, teacher_headers, "apitest-chin-" + token, "语文",
                          f"语文笔记 {token} 春晓默写。\n")

        r = requests.get(f"{base_url}/api/search", headers=teacher_headers,
                         params={"q": token, "subject": "数学", "mode": "keyword",
                                 "top_k": 20}, timeout=15)
        assert r.status_code == 200, r.text
        hits = r.json()["hits"]
        assert any(h["material_id"] == math_mid for h in hits), ("数学材料未命中", hits)
        assert all(h["material_id"] != wen_mid for h in hits), ("subject 未排除语文材料", hits)
    finally:
        for mid in (math_mid, wen_mid):
            if mid:
                _drop_material(db, mid)


def test_search_never_leaks_other_class(base_url, other_headers, db, class_id_of):
    # 用 B 班学生身份检索 A 班独有的内容，返回的命中不得含 A 班材料。
    cid_b = class_id_of("student_b1")
    r = requests.get(f"{base_url}/api/search", headers=other_headers,
                     params={"q": "分数 通分", "mode": "keyword"}, timeout=15)
    assert r.status_code == 200, r.text
    with db.get_conn() as conn:
        for h in r.json()["hits"]:
            row = db._one(conn, "SELECT class_id FROM materials WHERE id = %s",
                          (h["material_id"],))
            assert row is not None and int(row["class_id"]) == cid_b, h
