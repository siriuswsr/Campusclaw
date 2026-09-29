仓库根已有第 3 课归档后的 openspec/specs/auth-upload/ 与可运行的 CampusClaw 最小栈（浏览器 + Nginx + Go 服务 + MySQL，Docker Compose 部署）。第 3 课已实现登录、按班级隔离与教师上传入库（materials + knowledge_entries 同事务）。本 change 在其后追加检索能力；动机见 proposal.md，可验收行为见 specs/knowledge-retrieval/spec.md。
Goals / Non-Goals
Goals:
上传成功后，正文被切分为切片并写入 knowledge_chunks，每条切片对应一条 Qdrant 向量（主键一致）。
学生与教师均可对本班材料做 keyword / vector / hybrid 检索，命中结果可回溯至材料标题、切片序号与原文摘录。
POST /api/ask 仅在有本班命中切片时调用对话网关，回答带 [n] 出处标注；无命中返回固定文案、不调用模型。
班级标识仅从会话读取，关键字与向量两条路径均按会话班级过滤；跨班检索对外表现为无命中。
Compose 一键启动；Qdrant 不可用时 keyword 仍可用，vector/hybrid 返回 503。
Non-Goals:
见 proposal.md Non-goals（流式输出、重排序、编排框架、向量库暴露、前端替代服务端隔离等）。

技术栈与新增模块
环节
选择
说明
切分
Go 服务内实现三种策略
auto（最大 800 字、重叠 80 字，优先空行/换行/句号断开）、custom（最大 100–2000 字、重叠 0–50%，可预处理）、hierarchy（按 Markdown 标题分章，标题保留在切片内，某章过长再按窗口切）
嵌入
调用课程网关（兼容 OpenAI /embeddings）
仅在服务端调用；密钥不下发浏览器；向量维度与 Qdrant collection 保持一致
向量库
Compose 中的 Qdrant
集合 campusclaw_chunks，度量余弦；payload 仅含标识，不含正文；不向宿主机映射端口
关键字
MySQL FULLTEXT ... WITH PARSER ngram
token 长度 2；仅查 index_status = ready 的切片
混合排序
RRF（Reciprocal Rank Fusion）
k = 60；各路先独立过滤再按名次融合，缺席一路不贡献分数
对话
调用课程网关对话接口
仅在已取得切片后触发；输入由检索模块组装（材料标题、切片序号、切片正文、用户提问、此前对话），回答 [n] 与 citations 顺序一致
编排
不引入框架
Go 服务自行完成切分、嵌入与检索，便于按规约逐条验收

模块划分（在既有服务上扩展）
internal/
  auth/        # 第3课已有：登录会话、login_required、班级写入请求上下文
  materials/   # 第3课已有：材料列表、按ID访问、上传入库（materials + knowledge_entries 同事务）
  chunk/       # 新增：切分模块（auto/custom/hierarchy、预处理、偏移计算）
  embed/       # 新增：嵌入模块（文本→向量，调用课程网关）
  vector/      # 新增：Qdrant 读写（按 class_id 过滤、主键=knowledge_chunks.id）
  search/      # 新增：检索模块（keyword/vector/hybrid、班级过滤、RRF、回表取正文）
  chat/        # 新增：对话模块（组装切片+提问，调用对话网关，[n] 标注）
  api/         # 路由：POST /api/search、POST /api/ask、重建索引接口


上传入库数据流（第 4 课扩展）
Client (teacher, multipart/form-data) POST /api/materials/upload
 │
 ├─ 第3课链路：认证 → 教师角色 → 白名单校验 → 落盘 → 事务写 materials + knowledge_entries
 │
 └─ 第4课追加（事务提交后、异步或同流程）：
    ├─ 读取 body_text（不改写原文）
    ├─ 按本次策略切分 → 写入 knowledge_chunks（含字符偏移、index_status）
    ├─ 逐切片调用嵌入网关 → 每条向量写入 Qdrant（主键 = knowledge_chunks.id）
    └─ 嵌入失败：MySQL 记录保留、切片标记 failed、Qdrant 不写不完整向量

材料模块先保留原文；检索模块失败时原文仍然存在，仅这批切片无法用于向量检索。
重建索引：先删除旧切片与旧 Qdrant 向量，再按本次策略重新切分、嵌入、写入。

检索三条路径
模式
访问组件
排序
keyword
仅 MySQL 全文索引；不调用嵌入、不访问 Qdrant
按全文相关度由高到低
vector
问句嵌入 → Qdrant 按 class_id 过滤 → 余弦 < 0.35 丢弃 → 主键回 MySQL 取正文（再次核对班级）
按余弦相似度由高到低
hybrid
同时执行两条路径，各路先独立过滤，再按名次 RRF（k=60）融合
缺席一路不贡献分数

摘录始终取自 MySQL 的 chunk_text；Qdrant 不存正文。
Qdrant 不可用：keyword 仍返回结果；vector/hybrid 返回 503；不编造相似度分数。
空查询返回 400。
POST /api/ask：以混合模式取本班前 4 条切片；无候选 → 直接返回固定文案，不调用对话网关；有候选 → 对话模块收到材料标题、切片序号、切片正文、用户最新一句提问与此前对话；客户端注入的 system 消息丢弃，system 由服务端写入。

检索侧班级隔离
认证模块按 Cookie 将用户写入请求上下文（第 3 课已有）。
检索模块只从该上下文读取 class_id；查询字符串、JSON 正文、请求头中另行携带的 class_id 在解析后一律丢弃。
关键字路径：WHERE class_id = 会话班级 AND index_status = 'ready'。
向量路径：Qdrant 查询带 class_id 过滤；返回主键后再用同一班级条件回 MySQL 取正文。
跨班级检索对外表现为 HTTP 200 空 hits（或固定文案），不以 403/404 暗示资料归属；按材料标识打开详情沿用第 3 课规则（跨班与不存在同构 404）。

Docker Compose 扩展
文件约定
docker-compose.yml 在既有 web / api / db 基础上新增 qdrant 服务：
使用 Qdrant 官方镜像；不向宿主机映射端口（或仅映射到容器网络内部）。
volumes: qdrant-data:/qdrant/storage。
环境变量注入嵌入/对话网关密钥（env_file: .env）。
MySQL 全文索引：chunk_text 列建 FULLTEXT ... WITH PARSER ngram（token 长度 2）。
浏览器仍然只访问本站 nginx 暴露的端口；/api 反向代理至 Go；Qdrant 与两类模型网关均不作为浏览器入口。
健康与降级
GET /health 沿用第 3 课，不经过认证，仅存活探测。
Qdrant 不可用时 keyword 仍可用；vector/hybrid 返回 503，不编造相似度分数。

Decisions（摘要）
#
决策
备选
1
切片正文存 MySQL（knowledge_chunks），向量存 Qdrant，主键一致关联
将 chunk_text 写入 Qdrant payload（减少一次查询，但正文两份副本）
2
MySQL FULLTEXT ngram 做关键字检索
BM25 / ES（需重建索引与事务设计）
3
混合排序用 RRF（k=60），融合名次而非分数
分数归一化加权求和（换嵌入模型需重标定权重）
4
不引入编排框架，Go 自行完成
LangChain / LlamaIndex / Dify
5
跨班检索对外表现为无命中（200 空 hits）
返回 403（泄露资料存在性）
6
Qdrant 不映射端口，仅服务端可访问
映射端口便于调试（客户端可绕过认证）

Risks / Trade-offs
[嵌入模型变更] → 向量维度须与 Qdrant collection 保持一致；换模型需重建全部向量。
[Qdrant 故障] → keyword 降级可用；vector/hybrid 503，验收时须有降级记录。
[切片边界] → 重叠避免句子被窗口边界截断；auto 优先在空行/换行/句号断开。
[预处理偏移] → 预处理后偏移是相对预处理文本的，body_text 保持原文；文档中明确标注。
Migration Plan
第 3 课已归档的 auth-upload 行为不变。新增 knowledge_chunks 表与 Qdrant 集合；已有 knowledge_entries.body_text 在首次启动/种子补齐时按 auto 策略建立索引；已入库材料不会自动重新切分，切换策略须显式重建索引。
Open Questions
（课程原文未公开，以下以课件为准，建议向课程侧核实：POST /api/ask 的请求/响应字段命名；检索接口的精确路由与参数名。）