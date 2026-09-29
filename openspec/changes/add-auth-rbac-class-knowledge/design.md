## Context

Greenfield 项目，仓库当前仅有 OpenSpec 脚手架，无既有代码或技术债。本变更为校园教学平台引入第一块功能基石：身份认证、角色权限、班级隔离、教学材料上传。技术栈采用 Flask + SQLite（轻量、单文件 DB、零外部服务依赖，适合课程项目），材料文件存服务端磁盘目录，整体由 Docker Compose 编排。详细功能需求见 `specs/` 下三个 capability 的 spec。

## Goals / Non-Goals

**Goals:**

- 提供最小可运行的服务端骨架：Flask 应用 + SQLite + 磁盘文件存储，一键 `docker compose up` 启动
- 实现“认证—授权—班级隔离—材料读写”完整链路，满足三份 spec 的全部场景
- 把安全约束（密码哈希、密钥环境变量、服务端过滤）做成不可绕过的默认路径

**Non-Goals:**

见 proposal.md 的 Non-Goals 章节——检索问答、对话助手、作业提交与批改、SSO、生产 HA、前端 SPA、用户自助注册、材料版本管理、多租户隔离等均不在本课范围。

## Decisions

### D1. Flask + SQLite + Flask-SQLAlchemy

**选择**：Flask 作为 Web 框架，SQLite 作为数据库（单文件，零配置），Flask-SQLAlchemy 作 ORM。

**理由**：课程项目规模小，SQLite 无需独立数据库服务，部署最简；Flask 生态成熟，Flask-Login / Flask-SQLAlchemy 等扩展开箱即用。相比 FastAPI+PostgreSQL，减少了外部依赖组件数量，`docker compose up` 只需一个应用容器。

**备选**：FastAPI + PostgreSQL + MinIO（更贴近生产，但组件多、部署重）；Django（自带 admin 与 ORM，但偏离 Flask 选型且偏重）。

### D2. 会话管理：Flask-Login + 服务端 signed session

**选择**：使用 Flask-Login 管理用户会话，会话状态存于 Flask signed session cookie（由 `SECRET_KEY` 签名），客户端无法伪造。

**理由**：Flask-Login 是 Flask 生态标准方案，提供 `login_user` / `logout_user` / `current_user` / `@login_required` 等原语；signed cookie 无需服务端会话存储，部署简单。`SECRET_KEY` 从环境变量读取，缺失则拒绝启动。

**备选**：JWT 无状态令牌（需自行实现依赖注入，Flask 生态不如 FastAPI 顺手）；服务端 Redis 会话（多一个组件）。

**会话生命周期**：默认 1 天过期，可通过 `REMEMBER_COOKIE_DURATION` 配置；登出调用 `logout_user()` 清除会话。

### D3. 密码哈希：bcrypt（flask-bcrypt 或 passlib[bcrypt]）

**选择**：使用 bcrypt 哈希密码，通过 `flask-bcrypt` 扩展或 `passlib[bcrypt]` 提供 `generate_password_hash` / `check_password_hash`。

**理由**：bcrypt 是自适应 KDF，抗暴力破解；Flask 生态中 `flask-bcrypt` 是常用方案，API 简洁。Werkzeug 自带的 `generate_password_hash` 默认用 pbkdf2，强度足够但 bcrypt 更主流。

**实现约束**：
- 创建用户时 `password_hash = bcrypt.generate_password_hash(password).decode('utf-8')`
- 登录验证 `bcrypt.check_password_hash(user.password_hash, password)`
- 种子脚本中密码哈希在运行时生成，不硬编码明文

### D4. 文件存储：服务端磁盘目录 + SQLite 元数据

**选择**：上传文件写入服务端 `UPLOAD_DIR` 目录（Docker 卷挂载），元数据（标题、班级、上传者、文件路径、时间）写入 SQLite `materials` 表。

**理由**：相比 MinIO 对象存储，磁盘目录更简单，无需额外组件；SQLite 存元数据支持按班级索引查询。文件命名用 UUID 避免冲突，路径不携带班级信息。

**文件命名规则**：`<uuid>.<ext>`，存储于 `UPLOAD_DIR` 扁平目录；班级归属只在 DB 元数据中体现，不从文件路径推断。

**备选**：MinIO 对象存储（S3 兼容，适合多实例，但课程项目单实例下过度设计）；文件以 BLOB 存 SQLite（大文件场景差，DB 膨胀）。

### D5. 班级隔离 = 服务端查询强制 `class_id` 过滤

**选择**：所有材料查询走统一的“按当前用户 `class_id` 过滤”的服务方法；单条材料访问前先按 `(material_id, class_id)` 联合查询，查不到即 404，不区分“不存在”与“越权”以避免枚举。

**理由**：spec 要求过滤在 DB 的 WHERE 中完成，而非应用层裁剪全量。统一服务方法让“忘加过滤”成为难以犯的错——不存在不经过该方法的查询路径。

**实现约束**：
- `materials` 表对 `class_id` 建索引
- 材料服务层暴露的方法签名强制接收 `class_id` 参数，不提供“无条件查询”重载
- 文件下载端点先校验元数据归属再读取磁盘文件，绝不直接用客户端传入的文件路径跳过校验

**备选**：SQLite 无内置行级安全策略（RLS），应用层强制过滤是唯一可行方案。

### D6. RBAC：Flask-Login + 自定义角色装饰器

**选择**：在 `@login_required` 基础上增加 `@role_required('teacher')` 装饰器，路由组合使用。

**理由**：角色仅教师/学生两种，自定义装饰器十几行即可覆盖；不引入 Casbin 等 RBAC 库（过度设计）。

**实现**：
```python
def role_required(role):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if current_user.role != role:
                abort(403)
            return fn(*args, **kwargs)
        return wrapper
    return decorator
```

### D7. 上传数据流

**完整流程**：

```
教师客户端
  │  POST /materials (multipart/form-data: file, title)
  ▼
[Flask 路由] ──@login_required + @role_required('teacher')──► 403 if student
  │
  ▼
[材料服务]
  ├─ 生成 uuid 文件名
  ├─ 保存文件到 UPLOAD_DIR/<uuid>.<ext>
  ├─ 写入 materials 表 (title, class_id=current_user.class_id,
  │                      uploader_id, file_path, created_at)
  └─ 返回 material_id
  │
  ▼
本班列表查询 GET /materials
  └─ SELECT * FROM materials WHERE class_id = ? ORDER BY created_at DESC
     （class_id 来自 current_user，客户端不可覆盖）
```

**关键点**：
- `class_id` 取自 `current_user.class_id`，绝不从请求参数读取
- 文件保存与 DB 写入在同一逻辑单元；若 DB 写入失败则删除已存文件（补偿）
- 下载端点 `GET /materials/<id>/download`：先按 `(id, current_user.class_id)` 查元数据，查不到 404；查到后用 `send_file` 返回文件流

### D8. Docker Compose 编排 + 健康探针

**选择**：`docker-compose.yml` 含单个 `app` 服务（Flask + gunicorn/werkzeug），挂载 SQLite DB 文件卷与上传目录卷；`app` 的 `healthcheck` 调 `GET /health`。

**理由**：SQLite 与磁盘文件无需独立容器，Compose 仅需编排应用服务本身；健康探针复用 `service-health` capability 的端点。

**配置加载**：`python-dotenv` 从 `.env` 读配置（`SECRET_KEY`、`DATABASE_PATH`、`UPLOAD_DIR`），`.env` gitignored，`.env.example` 入库；启动时校验 `SECRET_KEY` 存在，缺失即拒绝启动。

### D9. 目录结构

```
app/
  __init__.py          # Flask app factory、路由挂载、健康端点
  config.py            # 配置加载（环境变量）
  models.py            # User / Class / Material ORM
  auth/
    routes.py          # /auth/login /auth/logout /auth/me
    forms.py           # 登录表单校验
  materials/
    routes.py          # 上传/列表/详情/下载路由
    service.py         # 业务逻辑（含 class_id 强制过滤）
  core/
    security.py        # 密码 hash/verify
    decorators.py      # role_required
  seed.py              # 预置数据脚本
  templates/
    login.html         # 最小登录页
tests/
  test_auth.py
  test_materials_isolation.py
  test_health.py
docker-compose.yml
Dockerfile
requirements.txt
.env.example
.gitignore
```

## Risks / Trade-offs

- **[班级过滤被某条新查询遗忘]** → 材料服务层只暴露强制 `class_id` 的方法，不提供无过滤重载；集成测试断言“跨班访问 → 404”作为回归护栏；代码审查重点核对是否出现绕过服务直查 `Material` 的路径。
- **[文件路径不带班级，存在越权下载风险]** → 下载必须先查元数据校验归属，再凭 file_path 读文件；禁止暴露静态文件目录的直接 HTTP 访问；测试覆盖跨班下载被拒。
- **[密钥误入仓库]** → `.gitignore` 排除 `.env`；`config.py` 校验 `SECRET_KEY` 缺失即报错；种子脚本不硬编码明文密码。
- **[SQLite 并发写入限制]** → 课程项目并发量低，SQLite 足够；若后续并发增长再迁 PostgreSQL，应用层通过 SQLAlchemy 抽象可平滑迁移。
- **[signed cookie 会话在过期前不可服务端吊销]** → TTL 设为 1 天；登出清除客户端 cookie；若需即时吊销再加服务端会话黑名单（后续迭代）。
- **[磁盘文件无冗余]** → Docker 卷持久化；定期备份卷即可；课程项目可接受。

## Migration Plan

Greenfield，无历史数据迁移：

1. Flask app 启动时若 SQLite DB 文件不存在，自动建表（`db.create_all()`）
2. 启动后检测 `users` 表是否为空，空则执行 `seed.py` 种子脚本：创建 2 个班级、每班 1 名教师 + 1 名学生（密码 bcrypt 哈希后入库）、每班 1 份示例材料
3. 上传目录若不存在则自动创建
4. `docker compose up` 一键拉起应用；`health` 通过后服务可用

回滚：本期为初版，无线上流量；如需回退，停服并清理容器与卷即可。

## Open Questions

- **上传文件大小上限**：spec 未限定。初版拟设单文件 50MB（Flask `MAX_CONTENT_LENGTH`）；如需更大文件再引入分片上传。此项可在实现时按默认值落地，不影响 spec。
- **允许的文件类型白名单**：spec 未限定。初版拟放行常见教学文档/图片/pdf，按扩展名与 MIME 双校验；具体白名单可在 tasks 实现时确定，不改变 spec 行为契约。


