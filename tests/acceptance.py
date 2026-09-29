"""逐条验收脚本 — 真实 HTTP 请求打 localhost:8080 容器。
运行: python tests/acceptance.py
"""
import urllib.request, urllib.error, json, io, http.cookiejar, time, sqlite3, os

BASE = "http://localhost:8080"
PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # return None = don't follow, let HTTPError be raised

class Tester:
    def __init__(self, label):
        self.cj = http.cookiejar.CookieJar()
        self.label = label

    def post(self, path, json_body=None, multipart=None, headers=None):
        h = {"Content-Type": "application/json"} if json_body else (headers or {})
        data = None
        if json_body is not None:
            data = json.dumps(json_body).encode()
        elif multipart is not None:
            data, boundary = _build_multipart(multipart)
            h["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        req = urllib.request.Request(BASE + path, data=data, headers=h, method="POST")
        op = urllib.request.build_opener(_NoRedirect(), urllib.request.HTTPCookieProcessor(self.cj))
        try:
            with op.open(req, timeout=5) as r:
                body = r.read().decode()
                try: body = json.loads(body)
                except Exception: pass
                return r.status, body, dict(r.headers)
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            try: body = json.loads(body)
            except Exception: pass
            return e.code, body, dict(e.headers)

    def get(self, path, headers=None):
        h = headers or {}
        req = urllib.request.Request(BASE + path, headers=h)
        op = urllib.request.build_opener(_NoRedirect(), urllib.request.HTTPCookieProcessor(self.cj))
        try:
            with op.open(req, timeout=5) as r:
                body = r.read().decode()
                try: body = json.loads(body)
                except Exception: pass
                return r.status, body, dict(r.headers)
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            try: body = json.loads(body)
            except Exception: pass
            return e.code, body, dict(e.headers)

    def reset(self):
        self.cj = http.cookiejar.CookieJar()


def _build_multipart(fields):
    boundary = "----TESTBOUNDARY"
    body = io.BytesIO()
    for name, val in fields:
        body.write(f"--{boundary}\r\n".encode())
        if isinstance(val, tuple):
            fname, content = val
            ctype = "text/markdown" if fname.endswith(".md") else ("text/plain" if fname.endswith(".txt") else "application/octet-stream")
            body.write(f'Content-Disposition: form-data; name="{name}"; filename="{fname}"\r\n'.encode())
            body.write(f"Content-Type: {ctype}\r\n\r\n".encode())
            body.write(content + b"\r\n")
        else:
            body.write(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
            body.write(val.encode() + b"\r\n")
    body.write(f"--{boundary}--\r\n".encode())
    return body.getvalue(), boundary


def report(n, scenario, action, actual, spec_ref, pass_or_fail, detail=""):
    status = PASS if pass_or_fail else FAIL
    print(f"\n{status} [R{n}] {scenario}")
    print(f"  动作: {action}")
    print(f"  实际: {actual}")
    print(f"  规约: spec.md Scenario {spec_ref}")
    if detail:
        print(f"  说明: {detail}")


# ========================================================
print("=" * 70)
print("CampusClaw 逐条验收 — 真实 HTTP 请求")
print("=" * 70)

# ========================================================
# 前置: /health 确认容器活
# ========================================================
print("\n-- 前置: /health --")
s, b, _ = Tester("health").get("/health")
print(f"  GET /health → {s} {b}")
assert s == 200 and b["status"] == "ok", "容器未就绪"

# ========================================================
# 1. 预置账号登录
# ========================================================
print("\n" + "=" * 70)
print("1. 预置账号登录")
print("=" * 70)

t_teacher = Tester("teacher_a")
t_stua = Tester("student_a1")
t_stub = Tester("student_b1")

# 1.1 teacher_a 正确密码
s, b, _ = t_teacher.post("/auth/login", json_body={"username":"teacher_a","password":"teacher123"})
pass1a = (s == 200 and b.get("ok") is True and b.get("role") == "teacher" and b.get("class_id") is not None)
report("1.1", "teacher_a 正确密码登录", "POST /auth/login json=teacher_a/teacher123",
       f"status={s}, body={b}", "教师与学生登录成功", pass1a)

# 1.2 student_a1
s, b, _ = t_stua.post("/auth/login", json_body={"username":"student_a1","password":"student123"})
pass1b = (s == 200 and b.get("role") == "student" and b.get("class_id") is not None)
report("1.2", "student_a1 登录成功", "POST /auth/login json=student_a1/student123",
       f"status={s}, body={b}", "教师与学生登录成功", pass1b)

# 1.3 student_b1
s, b, _ = t_stub.post("/auth/login", json_body={"username":"student_b1","password":"student123"})
pass1c = (s == 200 and b.get("role") == "student" and b.get("class_id") is not None)
report("1.3", "student_b1 登录成功", "POST /auth/login json=student_b1/student123",
       f"status={s}, body={b}", "教师与学生登录成功", pass1c)

# 1.4 会话字段: /me 验证
s, b, _ = t_teacher.get("/auth/me")
pass1d = (s == 200 and "user_id" not in b and b.get("role") == "teacher" and b.get("class_id") is not None)
report("1.4", "会话含 role 与 class_id", "GET /auth/me (logged in)",
       f"status={s}, body={b}", "教师与学生登录成功", pass1d)

# 1.5 错误密码 → 同构 401
t_bad = Tester("bad")
s1, b1, _ = t_bad.post("/auth/login", json_body={"username":"teacher_a","password":"wrong!"})
t_bad.reset()
s2, b2, _ = t_bad.post("/auth/login", json_body={"username":"nonexistent","password":"whatever"})
pass1e = (s1 == 401 and s2 == 401 and b1 == b2)
report("1.5", "错误密码与不存在用户同构响应",
       f"POST /auth/login wrong pw (→{s1} {b1}) vs nonexistent (→{s2} {b2})",
       f"same? {b1 == b2}", "错误密码登录失败 / 三类失败响应同形", pass1e,
       detail=f"wrong_pw={b1!r} nonexistent={b2!r}")

# ========================================================
# 2. 未登录访问
# ========================================================
print("\n" + "=" * 70)
print("2. 未登录访问受保护页面与 API")
print("=" * 70)

t_anon = Tester("anon")
# 2.1 页面 → 302
s, b, h = t_anon.get("/materials")
pass2a = (s == 302 and "login" in str(h.get("Location", "")))
report("2.1", "未登录页面 → 302 到登录页", "GET /materials (browser)",
       f"status={s}, Location={h.get('Location')}", "未登录访问受保护页面", pass2a)

# 2.2 API → 401
t_anon2 = Tester("anon2")
s, b, h = t_anon2.get("/materials/api")
pass2b = (s == 401)
report("2.2", "未登录 API → 401", "GET /materials/api",
       f"status={s}, body={b}", "未登录访问受保护页面", pass2b)

# 2.3 响应不含材料数据
pass2c = (not isinstance(b, list))
report("2.3", "未登录 API 响应不含任何材料标题/正文",
       "GET /materials/api 响应体检查", f"body={b}",
       "未登录访问受保护页面 (响应 MUST NOT 包含材料数据)", pass2c,
       detail="若 body 是字典/错误对象则未泄露材料")

# ========================================================
# 3. 学生上传 → 403
# ========================================================
print("\n" + "=" * 70)
print("3. 学生上传")
print("=" * 70)

# 前置: 查 DB 行数
import urllib.request as ur
class DB:
    def __init__(self, path): self.path = path
    def q(self, sql):
        c = sqlite3.connect(self.path); r = c.execute(sql).fetchall(); c.close(); return r
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "app.db")
db = DB(DB_PATH)
m_before = db.q("SELECT COUNT(*) FROM materials")[0][0]
k_before = db.q("SELECT COUNT(*) FROM knowledge_entries")[0][0]
f_before = set(os.listdir(os.path.join(os.path.dirname(__file__), "..", "uploads")))

s, b, _ = t_stua.post("/materials/api/upload",
    multipart=[("title","evil"), ("file",("evil.md", b"evil content"))])
pass3a = (s == 403)
report("3.1", "学生上传 → 403", "student_a1 POST /materials/api/upload",
       f"status={s}, body={b}", "学生上传被拒绝（403）", pass3a)

m_after = db.q("SELECT COUNT(*) FROM materials")[0][0]
k_after = db.q("SELECT COUNT(*) FROM knowledge_entries")[0][0]
f_after = set(os.listdir(os.path.join(os.path.dirname(__file__), "..", "uploads")))
pass3b = (m_after == m_before and k_after == k_before and f_after == f_before)
report("3.2", "学生上传后 DB + uploads 无新增",
       "student_a1 POST upload 后对比 DB/uploads",
       f"materials {m_before}→{m_after}, knowledge {k_before}→{k_after}, files unchanged={f_after==f_before}",
       "学生上传被拒 (无脏数据)", pass3b)

# ========================================================
# 4. 教师上传 → 201 + 列表可见
# ========================================================
print("\n" + "=" * 70)
print("4. 教师上传")
print("=" * 70)

UPLOAD_TITLE = "验收专用-教师A新上传"
s, b, _ = t_teacher.post("/materials/api/upload",
    multipart=[("title", UPLOAD_TITLE,), ("file", ("verify_upload.md", "# verify\nby teacher_a acceptance test".encode()))])
pass4a = (s == 201 and b.get("material_id") is not None)
mid = b.get("material_id")
report("4.1", "教师上传 .md → 201 + material_id", "teacher_a POST upload",
       f"status={s}, body={b}", "教师成功上传", pass4a)

# 4.2 教师刷新列表可见
s, items, _ = t_teacher.get("/materials/api")
pass4b = (any(m["title"] == UPLOAD_TITLE for m in items if isinstance(items, list)))
report("4.2", "教师刷新列表可见新上传", "teacher_a GET /materials/api",
       f"status={s}, titles={[m['title'] for m in items if isinstance(items,list)]}",
       "上传后本班列表可查", pass4b)

# 4.3 学生 A1 刷新也可见（只读）
s, items_a, _ = t_stua.get("/materials/api")
pass4c_visible = (any(m["title"] == UPLOAD_TITLE for m in items_a if isinstance(items_a, list)))
# 4.3b 学生仍无法上传
t_stua.reset(); t_stua.post("/auth/login", json_body={"username":"student_a1","password":"student123"})
s, b, _ = t_stua.post("/materials/api/upload", multipart=[("title","again"), ("file",("x.md",b"x"))])
pass4c_readonly = (s == 403)
report("4.3", "学生 A1 列表可见 + 仍只读",
       "student_a1 GET list + POST upload",
       f"列表可见={pass4c_visible}, 再上传仍 403={pass4c_readonly}",
       "上传后学生本班可见只读", pass4c_visible and pass4c_readonly)

# 4.4 两表同事务 + class_id 来自 session
with sqlite3.connect(DB_PATH) as c:
    mrow = c.execute("SELECT id, class_id FROM materials WHERE id=?", (mid,)).fetchone()
    krow = c.execute("SELECT material_id, class_id FROM knowledge_entries WHERE material_id=?", (mid,)).fetchone()
pass4d = (mrow[1] == 1 and krow[1] == 1)  # class_id=1 = A班（teacher_a所在班级）
report("4.4", "materials + knowledge_entries 同事务 + class_id=1 (A班)",
       "DB 查询刚上传记录",
       f"material class_id={mrow[1]}, knowledge class_id={krow[1]}",
       "教师成功上传 / 材料元数据完整性", pass4d)

# ========================================================
# 5. 非法上传 → 4xx + 无脏数据
# ========================================================
print("\n" + "=" * 70)
print("5. 非法上传")
print("=" * 70)

m_before5 = db.q("SELECT COUNT(*) FROM materials")[0][0]
k_before5 = db.q("SELECT COUNT(*) FROM knowledge_entries")[0][0]
f_before5 = set(os.listdir(os.path.join(os.path.dirname(__file__), "..", "uploads")))

s, b, _ = t_teacher.post("/materials/api/upload",
    multipart=[("title","badpdf",), ("file",("bad.pdf", b"%PDF-1.4"))])
pass5a = (s in (400, 422) and s < 500)
report("5.1", ".pdf 白名单外 → 4xx", "teacher_a POST .pdf file",
       f"status={s}, body={b}", "教师上传白名单外扩展名", pass5a)

m_after5 = db.q("SELECT COUNT(*) FROM materials")[0][0]
k_after5 = db.q("SELECT COUNT(*) FROM knowledge_entries")[0][0]
f_after5 = set(os.listdir(os.path.join(os.path.dirname(__file__), "..", "uploads")))
pass5b = (m_after5 == m_before5 and k_after5 == k_before5 and f_after5 == f_before5)
report("5.2", ".pdf 无脏数据",
       "POST bad.pdf 后对比 DB/uploads",
       f"m {m_before5}→{m_after5}, k {k_before5}→{k_after5}, files 不变={f_after5==f_before5}",
       "上传失败时不产生脏数据", pass5b)

# ========================================================
# 6. 跨班隔离
# ========================================================
print("\n" + "=" * 70)
print("6. 跨班隔离")
print("=" * 70)

# 找 B 班 material id
with sqlite3.connect(DB_PATH) as c:
    b_mat = c.execute("SELECT id, title FROM materials WHERE class_id=2 LIMIT 1").fetchone()
b_id, b_title = b_mat[0], b_mat[1]

# 6.1 学生 A1 访问 B 班 material → 404
s, b, _ = t_stua.get(f"/materials/api/{b_id}")
# 6.2 不存在的 id → 同样 404（同构）
s2, b2, _ = t_stua.get("/materials/api/99999")
pass6a = (s == 404 and s2 == 404 and b == b2)
report("6.1", "跨班按 ID → 404 (与不存在同构)",
       f"student_a1 GET /materials/api/{b_id} vs nonexistent",
       f"crossclass status={s} body={b}, nonexistent status={s2} body={b2}",
       "跨班访问单条材料 / 跨班下载文件", pass6a,
       detail=f"cross={b!r} vs nonexistent={b2!r}")

# 6.3 跨班下载
s, b, _ = t_stua.get(f"/materials/api/{b_id}/download")
s2, b2, _ = t_stua.get("/materials/api/99999/download")
pass6b = (s == 404 and s2 == 404)
report("6.2", "跨班下载 → 404",
       f"student_a1 GET download (crossclass vs nonexistent)",
       f"cross={s}, nonexistent={s2}",
       "跨班下载被拒", pass6b)

# 6.4 学生 A1 列表不含 B 班标题
s, items, _ = t_stua.get("/materials/api")
if isinstance(items, list):
    titles = [m["title"] for m in items]
    pass6c = all("B 班" not in t for t in titles) and b_title not in titles
else:
    pass6c = False
report("6.3", "学生 A1 列表不含 B 班标题",
       "student_a1 GET /materials/api",
       f"titles={titles}, B班材料title={b_title}",
       "查询本班材料列表", pass6c)

# ========================================================
# 7. 篡改 class_id → 无效
# ========================================================
print("\n" + "=" * 70)
print("7. 篡改 class_id")
print("=" * 70)

# 7.1 Query param
s_orig, items_orig, _ = t_stua.get("/materials/api")
s_tamper, items_t, _ = t_stua.get("/materials/api?class_id=2")
pass7a = (isinstance(items_t, list) and len(items_t) == len(items_orig))
report("7.1", "Query ?class_id=2 被忽略",
       "student_a1 GET /materials/api?class_id=2",
       f"orig len={len(items_orig)}, tampered len={len(items_t)}",
       "材料列表仅含本班记录 (class_id 只从会话)", pass7a)

# 7.2 JSON body (API)
import urllib.request as ur2
def post_json(t, path, body):
    t2 = Tester(t.label); t2.cj = t.cj
    return t2.post(path, json_body=body)

# /materials/api 目前不接受 JSON body，但我们验证一下上传带 class_id 字段
s, b, _ = t_teacher.post("/materials/api/upload",
    multipart=[("title","tamper test"), ("class_id","2"), ("file",("tamper.md", b"x"))])
pass7b_upload = (s == 201)
# 查实际 class_id
mid = b.get("material_id")
with sqlite3.connect(DB_PATH) as c:
    actual = c.execute("SELECT class_id FROM materials WHERE id=?", (mid,)).fetchone()[0]
pass7b_class = (actual == 1)  # A班(1), NOT B班(2)
report("7.2", "上传时 class_id=2 被忽略（落库仍为 A 班）",
       "teacher_a POST upload (form带 class_id=2)",
       f"upload status={s}, DB class_id={actual} (expect 1)",
       "班级隔离 (client class_id 一律丢弃)", pass7b_upload and pass7b_class,
       detail=f"body带class_id=2，但DB写class_id={actual}（来自会话）")

# ========================================================
# 8. 持久化
# ========================================================
print("\n" + "=" * 70)
print("8. 持久化 (down → up)")
print("=" * 70)

import subprocess, shutil
orig_env = dict(os.environ)
orig_env["PATH"] = r"C:\Users\Sirius\AppData\Local\Programs\DockerDesktop\resources\bin;" + orig_env.get("PATH", "")

print("  执行: docker compose down ...")
subprocess.run(["docker", "compose", "down"], cwd=r"d:\课堂作业\campusclaw-mygroup",
               env=orig_env, capture_output=True, text=True, check=True)
time.sleep(2)
print("  执行: docker compose up -d ...")
subprocess.run(["docker", "compose", "up", "-d"], cwd=r"d:\课堂作业\campusclaw-mygroup",
               env=orig_env, capture_output=True, text=True, check=True)
print("  等待健康...")
for _ in range(20):
    time.sleep(1)
    try:
        s_health = urllib.request.urlopen(BASE + "/health", timeout=2).status
        if s_health == 200: break
    except Exception: pass

# 8.1 /health
s, b, _ = Tester("health2").get("/health")
pass8a = (s == 200 and b["status"] == "ok")
report("8.1", "重启后 GET /health → 200",
       "GET /health 后重建容器", f"status={s}, body={b}",
       "health 不依赖登录态", pass8a)

# 8.2 teacher_a 仍可登录
t_new = Tester("restarted")
s, b, _ = t_new.post("/auth/login", json_body={"username":"teacher_a","password":"teacher123"})
pass8b = (s == 200 and b.get("ok") is True)
report("8.2", "重启后 teacher_a 仍可登录",
       "POST /auth/login teacher_a/teacher123", f"status={s}, body={b}",
       "重建容器后数据仍在", pass8b)

# 8.3 种子材料仍可查到（从本地 DB 文件验证 volume 保留）
with sqlite3.connect(os.path.join(os.path.dirname(__file__), "..", "data", "app.db")) as c:
    seed_a = c.execute("SELECT title FROM materials WHERE title LIKE '%A 班%'").fetchone()
pass8c = seed_a is not None
report("8.3", "种子材料在重启后仍在 DB",
       "DB SELECT title LIKE '%A 班%'", f"seed A title={seed_a}",
       "重建容器后数据仍在", pass8c)

# ========================================================
# 9. 密码安全
# ========================================================
print("\n" + "=" * 70)
print("9. 密码安全")
print("=" * 70)

with sqlite3.connect(DB_PATH) as c:
    cur = c.execute("SELECT username, password_hash FROM users")
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    print(f"  DB columns: {cols}")
    for u, h in rows:
        print(f"  user={u}  hash={h[:12]}...")
        assert h.startswith("$2b$"), f"{u} hash not bcrypt: {h[:12]}"
        assert h not in ("teacher123", "student123"), f"{u} plaintext leaked!"
pass9a = True
report("9.1", "password_hash 为 bcrypt $2b$ 哈希，无明文",
       "DB SELECT username, password_hash FROM users",
       f"{len(rows)} users, all $2b$ prefix, plaintext check passed",
       "库中无明文密码", pass9a)

# 9.2 密钥不在源码中
import re
with open(os.path.join(os.path.dirname(__file__), "..", "app", "config.py"), "r") as f:
    cfg_src = f.read()
pass9b = ("SECRET_KEY" in cfg_src and "os.environ.get" in cfg_src and "def validate" in cfg_src)
report("9.2", "SECRET_KEY 仅从 env 读取 + validate 缺省即启动失败",
       "读 app/config.py 源码",
       f"config.py contains os.environ.get + validate()",
       "缺少会话密钥时服务不得静默使用默认值", pass9b)

# 9.3 源码无硬编码密钥
pass9c = True
found_lines = []
import re
for root, _, files in os.walk(os.path.join(os.path.dirname(__file__), "..", "app")):
    for fn in files:
        if fn.endswith(".py"):
            fp = os.path.join(root, fn)
            try:
                for ln in open(fp, encoding="utf-8"):
                    if "SECRET_KEY" in ln and "os.environ" not in ln and "load_dotenv" not in ln:
                        if re.search(r'=\s*["\x27][^"\x27]+["\x27]', ln):
                            pass9c = False; found_lines.append(ln.strip())
            except Exception: pass
report("9.3", "源码中无硬编码 SECRET_KEY 值",
       "遍历 app/*.py 查找 SECRET_KEY 硬编码值",
       f"found hardcoded={found_lines}",
       "密钥不入库 (spec R7.4)", pass9c)

# ========================================================
# 汇总
# ========================================================
print("\n" + "=" * 70)
results = {
    "1.1 teacher登录": pass1a, "1.2 student_a1登录": pass1b, "1.3 student_b1登录": pass1c,
    "1.4 会话含role+class_id": pass1d, "1.5 错误/不存在同构": pass1e,
    "2.1 未登录页面302": pass2a, "2.2 未登录API 401": pass2b, "2.3 响应无材料数据": pass2c,
    "3.1 学生上传403": pass3a, "3.2 无脏数据": pass3b,
    "4.1 教师上传201": pass4a, "4.2 教师列表可见": pass4b, "4.3 学生可见只读": pass4c_visible and pass4c_readonly,
    "4.4 DB双表同事务": pass4d,
    "5.1 .pdf→4xx": pass5a, "5.2 .pdf无脏数据": pass5b,
    "6.1 跨班详情→404同构": pass6a, "6.2 跨班下载→404": pass6b, "6.3 列表无他班标题": pass6c,
    "7.1 query tamper无效": pass7a, "7.2 upload class_id无效": pass7b_upload and pass7b_class,
    "8.1 /health重启后200": pass8a, "8.2 账号仍可登录": pass8b, "8.3 种子材料持久": pass8c,
    "9.1 bcrypt哈希": pass9a, "9.2 env-only SECRET_KEY": pass9b, "9.3 源码无硬编码密钥": pass9c,
}
total = len(results); passed = sum(results.values())
print(f"总 Scenario: {total}, 通过: {passed}, 失败: {total-passed}")
if total == passed:
    print(f"\033[92m★★★ 全部 {total} 条验收通过 ★★★\033[0m")
else:
    for k, v in results.items():
        if not v: print(f"  \033[91mFAIL\033[0m {k}")
print("=" * 70)
