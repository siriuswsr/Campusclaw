## Why

校园教学平台目前缺少身份认证与数据隔离能力：任何人可直接访问受保护页面并上传材料，且没有按班级区分的访问边界，存在越权与数据泄露风险。本变更引入用户登录、角色权限（教师/学生）、班级级数据隔离，并允许教师将教学材料上传至知识库供本班学生查阅，作为后续教学功能的基础。

## What Changes

- 新增用户认证：教师与学生使用账号密码登录；未登录访问受保护页面时引导至登录页
- 新增角色权限：区分教师/学生角色；学生调用上传接口必须被拒绝（服务端鉴权，非前端隐藏按钮）
- 新增班级隔离：班级作为数据边界，A 班用户访问 B 班材料必须被拒绝；过滤在服务端强制执行
- 新增教学材料上传：教师可上传材料至知识库（文件存磁盘 + SQLite 元数据），本班材料列表可查到该记录
- 新增预置数据：启动时通过种子脚本建立班级、教师、学生账号与示例材料
- 密码必须哈希存储，禁止明文；密钥仅放服务端环境变量，不进代码库
- 启动方式采用 Docker Compose；应用提供 `GET /health` 健康检查端点

- 用户登录方式改为 Token 鉴权：
  - POST /api/login 验证账号密码后返回 {"token": "eyJ...", "user": {"id":..., "role":..., "class_id":...}}
  - token 采用 JWT（HS256），载荷含 user_id / role / class_id / exp
  - 后续请求在 Authorization: Bearer <token> 头中携带 token
  - 客户端登录后将 token 写入 localStorage，登出时删除
  - 密钥仅存服务端环境变量 JWT_SECRET

## Non-Goals

本课范围明确不做以下内容（后续迭代再考虑）：

- 不做知识库检索问答（RAG / 向量检索 / 语义搜索）
- 不做对话助手 / AI 聊天机器人
- 不做作业提交与批改（作业上传、评分、反馈流程）
- 不做 SSO / 单点登录 / 第三方登录（OAuth、CAS、LDAP）
- 不做生产级高可用（多副本、负载均衡、自动扩缩容、数据库主从、异地备份）
- 不做前端 SPA（仅提供 JSON API 与登录页最小 HTML，满足“未登录跳转登录页”行为）
- 不做用户自助注册 / 改密 / 找回密码（用户与班级映射由预置种子数据建立）
- 不做材料版本管理、在线预览、全文检索、文件转码
- 不做多学校 / 多租户隔离，班级隔离是最外层数据边界
- 不做审计日志、操作留痕、细粒度权限策略引擎

## Capabilities

### New Capabilities

- `user-auth`: 用户登录（账号密码校验后签发 JWT）、角色区分（教师/学生）、密码哈希存储、Bearer token 身份承载（无 session/cookie）、未登录访问受保护页面引导登录页、预置班级/用户/材料数据
- `class-materials`: 教学材料的班级隔离存储——教师上传至知识库（磁盘文件 + SQLite 元数据）、本班材料列表查询、服务端强制班级过滤、跨班访问拒绝、学生上传拒绝、上传后本班列表可查
- `service-health`: 应用健康检查端点 `GET /health`，返回应用及关键依赖（数据库、文件存储）连通状态，供 Docker Compose 与运维监控使用

### Modified Capabilities

<!-- 无现有 specs，greenfield 项目全部为新增 -->

## Impact

- 新增代码：Flask 应用（认证路由、材料路由、中间件、DB 模型、文件存储客户端、健康检查端点）
- 新增依赖：Flask、PyJWT、bcrypt（或 werkzeug.security）、python-dotenv、pytest
- 新增基础设施：SQLite（单文件数据库，随容器卷持久化）、文件存储目录（挂载卷）
- 新增配置：环境变量承载密钥与路径信息（`JWT_SECRET`、`DATABASE_PATH`、`UPLOAD_DIR` 等）；提供 `.env.example` 模板，真实密钥不入库
- 新增端点：`POST /auth/login`、`GET /auth/logout`、`GET /auth/me`、`POST /materials`（教师）、`GET /materials`（按班级过滤）、`GET /materials/<id>`、`GET /materials/<id>/download`、`GET /health`
- 新增种子数据：启动时初始化班级、教师/学生账号（密码哈希入库）、示例材料
