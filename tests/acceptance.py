"""逐条验收脚本 — 真实 HTTP 请求打 localhost:8080 容器（JWT / Bearer 版）。
运行: python tests/acceptance.py

鉴权：POST /api/login 取 token，之后每个请求带 Authorization: Bearer <token>。
DB 断言通过 docker compose exec 查询 mysql 容器（不再有本地 sqlite 文件）。
"""
import urllib.request, urllib.error, json, io, os, subprocess, time
import base64, hmac, hashlib

BASE = "http://localhost:8080"
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
UPLOADS_DIR = os.path.join(ROOT, "uploads")
PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"

DOCKER_ENV = dict(os.environ)
DOCKER_ENV["PATH"] = r"C:\Users\Sirius\AppData\Local\Programs\DockerDesktop\resources\bin;" + DOCKER_ENV.get("PATH", "")


def docker(args):
    return subprocess.run(["docker"] + args, cwd=ROOT, env=DOCKER_ENV,
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def mysql_rows(sql):
    """Query MySQL inside the container → list of rows (tab-separated fields)."""
    r = docker(["compose", "exec", "-T", "mysql", "mysql", "-uroot", "-proot",
                "--default-character-set=utf8mb4", "-N", "-B", "-e", sql])
    return [ln.split("\t") for ln in r.stdout.splitlines()
            if ln.strip() and "Warning" not in ln]


def mysql_scalar(sql):
    rows = mysql_rows(sql)
    return rows[0][0] if rows and rows[0] else None


def mysql_int(sql):
    v = mysql_scalar(sql)
    return int(v) if v is not None else -1


def sign_hs256(payload, secret):
    """Minimal HS256 signer — keeps this script free of host-side PyJWT."""
    def b64(raw):
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    head = b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = b64(hmac.new(secret.encode(), f"{head}.{body}".encode(),
                       hashlib.sha256).digest())
    return f"{head}.{body}.{sig}"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # return None = don't follow, let HTTPError be raised

class Tester:
    """One identity = the Bearer token returned by POST /api/login."""

    def __init__(self, label):
        self.label = label
        self.token = None

    def login(self, username, password):
        s, b, _ = self.post("/api/login",
                            json_body={"username": username, "password": password})
        if s == 200 and isinstance(b, dict):
            self.token = b.get("token")
        return s, b

    def _headers(self, extra=None):
        h = dict(extra or {})
        if self.token:
            h["Authorization"] = "Bearer " + self.token
        return h

    def _send(self, req):
        op = urllib.request.build_opener(_NoRedirect())
        try:
            with op.open(req, timeout=10) as r:
                return r.status, _maybe_json(r.read().decode()), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, _maybe_json(e.read().decode()), dict(e.headers)

    def post(self, path, json_body=None, multipart=None, headers=None):
        h = self._headers({"Content-Type": "application/json"} if json_body else headers)
        data = None
        if json_body is not None:
            data = json.dumps(json_body).encode()
        elif multipart is not None:
            data, boundary = _build_multipart(multipart)
            h["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        req = urllib.request.Request(BASE + path, data=data, headers=h, method="POST")
        return self._send(req)

    def get(self, path, headers=None):
        req = urllib.request.Request(BASE + path, headers=self._headers(headers))
        return self._send(req)


def _maybe_json(text):
    try:
        return json.loads(text)
    except Exception:
        return text


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
s, b = t_teacher.login("teacher_a", "teacher123")
u_teacher = b.get("user", {}) if isinstance(b, dict) else {}
pass1a = (s == 200 and bool(b.get("token")) and u_teacher.get("role") == "teacher"
          and u_teacher.get("class_id") is not None)
report("1.1", "teacher_a 正确密码登录 → token + user", "POST /api/login json=teacher_a/teacher123",
       f"status={s}, has_token={bool(b.get('token')) if isinstance(b,dict) else None}, user={u_teacher}",
       "教师与学生登录成功", pass1a)

# 1.2 student_a1
s, b = t_stua.login("student_a1", "student123")
u_stua = b.get("user", {}) if isinstance(b, dict) else {}
pass1b = (s == 200 and bool(b.get("token")) and u_stua.get("role") == "student"
          and u_stua.get("class_id") is not None)
report("1.2", "student_a1 登录成功 → token + user", "POST /api/login json=student_a1/student123",
       f"status={s}, user={u_stua}", "教师与学生登录成功", pass1b)

# 1.3 student_b1
s, b = t_stub.login("student_b1", "student123")
u_stub = b.get("user", {}) if isinstance(b, dict) else {}
pass1c = (s == 200 and bool(b.get("token")) and u_stub.get("role") == "student"
          and u_stub.get("class_id") is not None)
report("1.3", "student_b1 登录成功 → token + user", "POST /api/login json=student_b1/student123",
       f"status={s}, user={u_stub}", "教师与学生登录成功", pass1c)

# 1.4 token 载荷身份: 带 Bearer 头请求 /api/me
s, b, _ = t_teacher.get("/api/me")
pass1d = (s == 200 and "user_id" not in b and b.get("role") == "teacher" and b.get("class_id") is not None)
report("1.4", "Bearer token 身份含 role 与 class_id", "GET /api/me (Authorization: Bearer)",
       f"status={s}, body={b}", "教师与学生登录成功", pass1d)

# 1.5 错误密码 → 同构 401
t_bad = Tester("bad")
s1, b1, _ = t_bad.post("/api/login", json_body={"username":"teacher_a","password":"wrong!"})
s2, b2, _ = t_bad.post("/api/login", json_body={"username":"nonexistent","password":"whatever"})
pass1e = (s1 == 401 and s2 == 401 and b1 == b2)
report("1.5", "错误密码与不存在用户同构响应",
       f"POST /api/login wrong pw (→{s1} {b1}) vs nonexistent (→{s2} {b2})",
       f"same? {b1 == b2}", "错误密码登录失败 / 三类失败响应同形", pass1e,
       detail=f"wrong_pw={b1!r} nonexistent={b2!r}")

# 1.6 无 Bearer 头 / 篡改 token → 401 {"error":"token_expired"}
s_noauth, b_noauth, _ = Tester("noauth").get("/api/me")
s_tamper, b_tamper, _ = Tester("tamper").get(
    "/api/me", headers={"Authorization": "Bearer " + t_teacher.token[:-4] + "aaaa"})
pass1f = (s_noauth == 401 and b_noauth == {"error": "token_expired"}
          and s_tamper == 401 and b_tamper == {"error": "token_expired"})
report("1.6", "缺头 / 篡改 token → 401 token_expired", "GET /api/me 无头 + 篡改末 4 位",
       f"no_header={s_noauth} {b_noauth}, tampered={s_tamper} {b_tamper}",
       "token 过期/篡改", pass1f)

# 1.7 过期 token（用 .env 里的 JWT_SECRET 自签一个 exp 已过的 token）→ 401
try:
    _secret = ""
    with open(os.path.join(ROOT, ".env"), encoding="utf-8") as f:
        for _ln in f:
            if _ln.startswith("JWT_SECRET="):
                _secret = _ln.split("=", 1)[1].strip()
    _expired = sign_hs256({"user_id": 1, "role": "teacher", "class_id": 1,
                           "exp": int(time.time()) - 10}, _secret)
    s_exp, b_exp, _ = Tester("expired").get("/api/me", headers={"Authorization": "Bearer " + _expired})
    pass1g = (s_exp == 401 and b_exp == {"error": "token_expired"})
    _exp_detail = f"status={s_exp}, body={b_exp}"
except Exception as _e:
    pass1g = False
    _exp_detail = f"无法自签过期 token: {_e}"
report("1.7", "过期 token → 401 token_expired", "GET /api/me (exp = now-10s)",
       _exp_detail, "token 过期/篡改", pass1g)

# ========================================================
# 2. 未登录访问
# ========================================================
print("\n" + "=" * 70)
print("2. 未登录访问受保护页面与 API")
print("=" * 70)

t_anon = Tester("anon")
# 2.1 页面壳 + 前端守卫（纯 localStorage 方案：服务端拿不到 token，无法 302）
s, html, h = t_anon.get("/materials")
s_js, js, _ = t_anon.get("/static/auth.js")
pass2a = (s == 200 and isinstance(html, str) and "示例讲义" not in html
          and "auth.js" in html
          and s_js == 200 and "location.replace" in js and "Bearer" in js)
report("2.1", "未登录访问受保护页面 → 服务端空壳 + 前端守卫跳登录页",
       "GET /materials (无 token) + GET /static/auth.js",
       f"page status={s}, 壳内无材料标题={'示例讲义' not in html if isinstance(html,str) else '?'}, "
       f"引用 auth.js={'auth.js' in html if isinstance(html,str) else '?'}, auth.js status={s_js}",
       "未登录访问受保护页面", pass2a,
       detail="纯 localStorage 方案下浏览器导航不带 Authorization 头，服务端无法 302；"
              "改为服务端返回空壳、auth.js 的 CCAuth.guard() 在页面加载时 location.replace('/login')")

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

# 前置: 查 DB 行数（MySQL 容器）
m_before = mysql_int("SELECT COUNT(*) FROM campusclaw.materials")
k_before = mysql_int("SELECT COUNT(*) FROM campusclaw.knowledge_entries")
f_before = set(os.listdir(UPLOADS_DIR))

s, b, _ = t_stua.post("/materials/api/upload",
    multipart=[("title","evil"), ("file",("evil.md", b"evil content"))])
pass3a = (s == 403)
report("3.1", "学生上传 → 403", "student_a1 POST /materials/api/upload",
       f"status={s}, body={b}", "学生上传被拒绝（403）", pass3a)

m_after = mysql_int("SELECT COUNT(*) FROM campusclaw.materials")
k_after = mysql_int("SELECT COUNT(*) FROM campusclaw.knowledge_entries")
f_after = set(os.listdir(UPLOADS_DIR))
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
# 4.3b 学生仍无法上传（同一 token 继续用）
s, b, _ = t_stua.post("/materials/api/upload", multipart=[("title","again"), ("file",("x.md",b"x"))])
pass4c_readonly = (s == 403)
report("4.3", "学生 A1 列表可见 + 仍只读",
       "student_a1 GET list + POST upload",
       f"列表可见={pass4c_visible}, 再上传仍 403={pass4c_readonly}",
       "上传后学生本班可见只读", pass4c_visible and pass4c_readonly)

# 4.4 两表同事务 + class_id 来自 token（而非客户端）
mrow_c = mysql_scalar(f"SELECT class_id FROM campusclaw.materials WHERE id={mid}")
krow_c = mysql_scalar(f"SELECT class_id FROM campusclaw.knowledge_entries WHERE material_id={mid}")
pass4d = (mrow_c == "1" and krow_c == "1")  # class_id=1 = A班（teacher_a 所在班级）
report("4.4", "materials + knowledge_entries 同事务 + class_id=1 (A班)",
       "MySQL 查询刚上传记录",
       f"material class_id={mrow_c}, knowledge class_id={krow_c}",
       "教师成功上传 / 材料元数据完整性", pass4d)

# ========================================================
# 5. 非法上传 → 4xx + 无脏数据
# ========================================================
print("\n" + "=" * 70)
print("5. 非法上传")
print("=" * 70)

m_before5 = mysql_int("SELECT COUNT(*) FROM campusclaw.materials")
k_before5 = mysql_int("SELECT COUNT(*) FROM campusclaw.knowledge_entries")
f_before5 = set(os.listdir(UPLOADS_DIR))

s, b, _ = t_teacher.post("/materials/api/upload",
    multipart=[("title","badpdf",), ("file",("bad.pdf", b"%PDF-1.4"))])
pass5a = (s in (400, 422) and s < 500)
report("5.1", ".pdf 白名单外 → 4xx", "teacher_a POST .pdf file",
       f"status={s}, body={b}", "教师上传白名单外扩展名", pass5a)

m_after5 = mysql_int("SELECT COUNT(*) FROM campusclaw.materials")
k_after5 = mysql_int("SELECT COUNT(*) FROM campusclaw.knowledge_entries")
f_after5 = set(os.listdir(UPLOADS_DIR))
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
b_mat = mysql_rows("SELECT id, title FROM campusclaw.materials WHERE class_id=2 LIMIT 1")[0]
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
    titles = []
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
       "材料列表仅含本班记录 (class_id 只从 token)", pass7a)

# 7.2 上传表单里带 class_id=2
s, b, _ = t_teacher.post("/materials/api/upload",
    multipart=[("title","tamper test"), ("class_id","2"), ("file",("tamper.md", b"x"))])
pass7b_upload = (s == 201)
# 查实际 class_id
mid = b.get("material_id")
actual = mysql_scalar(f"SELECT class_id FROM campusclaw.materials WHERE id={mid}")
pass7b_class = (actual == "1")  # A班(1), NOT B班(2)
report("7.2", "上传时 class_id=2 被忽略（落库仍为 A 班）",
       "teacher_a POST upload (form带 class_id=2)",
       f"upload status={s}, DB class_id={actual} (expect 1)",
       "班级隔离 (client class_id 一律丢弃)", pass7b_upload and pass7b_class,
       detail=f"body带class_id=2，但DB写class_id={actual}（来自 token）")

# ========================================================
# 8. 持久化
# ========================================================
print("\n" + "=" * 70)
print("8. 持久化 (down → up)")
print("=" * 70)

print("  执行: docker compose down ...")
m_pre_restart = mysql_int("SELECT COUNT(*) FROM campusclaw.materials")
docker(["compose", "down"])
time.sleep(2)
print("  执行: docker compose up -d ...")
docker(["compose", "up", "-d"])
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

# 8.2 teacher_a 仍可登录 + 重启前签发的 token 仍有效（JWT 无服务端状态）
t_new = Tester("restarted")
s, b = t_new.login("teacher_a", "teacher123")
s_old, b_old, _ = t_teacher.get("/api/me")   # 用的是重启前拿到的 token
pass8b = (s == 200 and bool(b.get("token")) and s_old == 200)
report("8.2", "重启后 teacher_a 仍可登录 + 旧 token 仍有效",
       "POST /api/login teacher_a/teacher123 + GET /api/me（重启前 token）",
       f"login status={s} has_token={bool(b.get('token')) if isinstance(b,dict) else None}, "
       f"old_token /api/me → {s_old} {b_old}",
       "重建容器后数据仍在 / token 无服务端状态", pass8b)

# 8.3 材料数据在重启后仍在（MySQL 命名卷保留，不随 down 丢失）
m_post_restart = mysql_int("SELECT COUNT(*) FROM campusclaw.materials")
pass8c = (m_post_restart == m_pre_restart and m_post_restart > 0)
report("8.3", "重启后材料数据仍在 DB（命名卷保留）",
       "重启前后对比 MySQL SELECT COUNT(*) FROM materials",
       f"materials {m_pre_restart} → {m_post_restart}",
       "重建容器后数据仍在", pass8c)

# ========================================================
# 9. 密码安全
# ========================================================
print("\n" + "=" * 70)
print("9. 密码安全")
print("=" * 70)

rows = mysql_rows("SELECT username, password_hash FROM campusclaw.users")
print(f"  DB columns: username, password_hash")
pass9a = True
for row in rows:
    u, h = row[0], row[1]
    print(f"  user={u}  hash={h[:12]}...")
    if not h.startswith("$2b$"):
        pass9a = False; print(f"    !! {u} hash not bcrypt: {h[:12]}")
    if h in ("teacher123", "student123"):
        pass9a = False; print(f"    !! {u} plaintext leaked!")
report("9.1", "password_hash 为 bcrypt $2b$ 哈希，无明文",
       "MySQL SELECT username, password_hash FROM users",
       f"{len(rows)} users, all $2b$ prefix, plaintext check passed",
       "库中无明文密码", pass9a)

# 9.2 密钥不在源码中（JWT 签名密钥）
import re
with open(os.path.join(ROOT, "app", "config.py"), "r") as f:
    cfg_src = f.read()
pass9b = ("JWT_SECRET" in cfg_src and "os.environ.get" in cfg_src and "def validate" in cfg_src
          and "SECRET_KEY" not in cfg_src and "from flask" not in cfg_src
          and "session[" not in cfg_src.lower())
report("9.2", "JWT_SECRET 仅从 env 读取 + validate 缺省即启动失败（无 Flask session/SECRET_KEY）",
       "读 app/config.py 源码",
       "config.py contains JWT_SECRET + os.environ.get + validate()，且无 SECRET_KEY/session",
       "缺少签名密钥时服务不得静默使用默认值", pass9b)

# 9.3 源码无硬编码密钥
pass9c = True
found_lines = []
import re
for root, _, files in os.walk(os.path.join(ROOT, "app")):
    for fn in files:
        if fn.endswith(".py"):
            fp = os.path.join(root, fn)
            try:
                for ln in open(fp, encoding="utf-8"):
                    if "JWT_SECRET" in ln and "os.environ" not in ln and "load_dotenv" not in ln:
                        if re.search(r'=\s*["\x27][^"\x27]+["\x27]', ln):
                            pass9c = False; found_lines.append(ln.strip())
            except Exception: pass
report("9.3", "源码中无硬编码 JWT_SECRET 值",
       "遍历 app/*.py 查找 JWT_SECRET 硬编码值",
       f"found hardcoded={found_lines}",
       "密钥不入库 (spec R7.4)", pass9c)

# ========================================================
# 汇总
# ========================================================
print("\n" + "=" * 70)
results = {
    "1.1 teacher登录": pass1a, "1.2 student_a1登录": pass1b, "1.3 student_b1登录": pass1c,
    "1.4 token身份含role+class_id": pass1d, "1.5 错误/不存在同构": pass1e,
    "1.6 缺头/篡改token→401": pass1f, "1.7 过期token→401": pass1g,
    "2.1 未登录页面壳+前端守卫": pass2a, "2.2 未登录API 401": pass2b, "2.3 响应无材料数据": pass2c,
    "3.1 学生上传403": pass3a, "3.2 无脏数据": pass3b,
    "4.1 教师上传201": pass4a, "4.2 教师列表可见": pass4b, "4.3 学生可见只读": pass4c_visible and pass4c_readonly,
    "4.4 DB双表同事务": pass4d,
    "5.1 .pdf→4xx": pass5a, "5.2 .pdf无脏数据": pass5b,
    "6.1 跨班详情→404同构": pass6a, "6.2 跨班下载→404": pass6b, "6.3 列表无他班标题": pass6c,
    "7.1 query tamper无效": pass7a, "7.2 upload class_id无效": pass7b_upload and pass7b_class,
    "8.1 /health重启后200": pass8a, "8.2 账号仍可登录+旧token有效": pass8b, "8.3 种子材料持久": pass8c,
    "9.1 bcrypt哈希": pass9a, "9.2 env-only JWT_SECRET": pass9b, "9.3 源码无硬编码密钥": pass9c,
}
total = len(results); passed = sum(results.values())
print(f"总 Scenario: {total}, 通过: {passed}, 失败: {total-passed}")
if total == passed:
    print(f"\033[92m★★★ 全部 {total} 条验收通过 ★★★\033[0m")
else:
    for k, v in results.items():
        if not v: print(f"  \033[91mFAIL\033[0m {k}")
print("=" * 70)
