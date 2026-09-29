# CampusClaw — 教学材料与知识库底座

**价值**：教师登录上传本班教学材料，学生查阅本班内容；班级为数据边界，A 班用户无法读取 B 班数据。
**场景**：面向中小学教研场景，提供登录、RBAC、班级隔离、材料上传入库最小可运行栈。
**不做**：不做 RAG/向量检索、对话助手、作业流程、SSO/OAuth、生产级高可用；仅用 Flask + SQLite + Docker Compose 单实例演示环境。

## 快速启动

```bash
cp .env.example .env          # 填入 SECRET_KEY（随意长串即可，不要提交真实密钥）
docker compose up --build     # 一键构建并启动
```

然后访问 `http://localhost:8080/login`。

## 预置账号

| 用户名       | 密码        | 角色   | 班级 |
|--------------|-------------|--------|------|
| `teacher_a`  | `teacher123`| 教师   | A    |
| `student_a1` | `student123`| 学生   | A    |
| `student_b1` | `student123`| 学生   | B    |

## 安全约定

- **跨班访问统一返回 404**（与「记录不存在」同构响应，避免枚举）。
- **密码以 bcrypt 加盐慢哈希存储**（`$2b$` 前缀），禁止明文。
- **SECRET_KEY 仅从环境变量读取**，缺失即启动失败，无内置默认值。
- **上传目录扁平存储** `uploads/<uuid>_<filename>`，class_id 仅在 DB 元数据中维护，不写入文件路径。
- **账号不存在与口令错误对外同构响应**，不返回 429 或区分性提示；限流仅记日志。
- **GET /health 仅做进程存活判定**，不查询数据库；数据库故障时业务接口返回 503（会话仍有效）。

## 运行

- `GET /health`：存活判定，无需登录。
- `GET /materials`（受保护）：教师上传/查看本班材料；学生只读。
- 数据库文件与上传目录通过 Compose volume 挂载，`down` 后重建不丢失。

## 开发（非 Compose）

```bash
pip install -r requirements.txt
cp .env.example .env && 填入 SECRET_KEY
python -m flask run --port 8080
```
