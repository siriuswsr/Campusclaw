用法：在 Trae 中按节实现，每完成一小节运行该节 verify:；checkbox 由 [ ] 改为 [x]。

## T1 数据模型与索引

- [x] 1.1 新增 knowledge_chunks 表：id、class_id、material_id、knowledge_entry_id、chunk_index、chunk_text、字符偏移（start_offset/end_offset）、index_status（ready/failed）、created_at — verify: sqlite3/mysql .schema 含上述字段，chunk_text 上有 FULLTEXT ... WITH PARSER ngram（token 长度 2）
- [x] 1.2 第 3 课 knowledge_entries 表保持 body_text 为上传原文，无预处理改写 — verify: 上传一份含 URL/连续空白的 .md 后，body_text 与原文一致
- [x] 1.3 Compose 增加 qdrant 服务（集合 campusclaw_chunks，度量余弦，不映射宿主机端口，数据卷持久化）— verify: docker compose up 后 Qdrant 容器 running，docker ps 无 6333/6334 映射到宿主机

## T2 切分模块

- [x] 2.1 实现 auto：最大 800 字、重叠 80 字，优先在空行/换行/句号处断开；未指定策略时默认 — verify: 对一份 >1000 字正文切分，切片条数与最大长度符合窗口规则
- [x] 2.2 实现 custom：最大长度 100–2000 字、重叠比例 0–50%；无断点处按最大长度强制截断；可选预处理（移除 URL/邮箱、折叠连续空白为单个空格）— verify: 对带 URL/连续空白的文本预处理后切分，切片不含 URL、空白被折叠；body_text 仍为原文
- [x] 2.3 实现 hierarchy：按 #/##/### 标题分章，标题保留在切片内；某章过长再按窗口切 — verify: 对含三级标题的 .md 切分，每章标题出现在对应切片内
- [x] 2.4 切分模块不调用嵌入服务、不写 Qdrant；输出含切片文本、序号与字符位置 — verify: 切分模块日志无嵌入/Qdrant 调用；切片偏移与预处理后文本一致

## T3 嵌入与向量入库

- [x] 3.1 嵌入模块调用课程网关（兼容 OpenAI /embeddings），密钥仅服务端环境变量，维度与 Qdrant collection 一致 — verify: 单条切片嵌入成功返回定长浮点向量；浏览器页面无密钥
- [x] 3.2 入库流程：保存 body_text → 切分写 knowledge_chunks → 每切片嵌入 → 写 Qdrant（payload 仅 class_id/material_id/knowledge_entry_id/chunk_id/chunk_index，向量主键 = knowledge_chunks.id）— verify: 上传一份材料后，knowledge_chunks 行数与 Qdrant 向量数一致，主键一致，payload 无 chunk_text
- [x] 3.3 嵌入失败：MySQL 记录保留、切片标记 failed、Qdrant 不写不完整向量 — verify: 停用嵌入网关后上传，knowledge_chunks 有 failed 行、Qdrant 无对应向量、材料记录仍在
- [x] 3.4 重建索引：先删旧切片与旧向量，再按本次策略重新切分/嵌入/写入 — verify: 对同一材料切换 auto→hierarchy 重建后，旧 knowledge_chunks 行数与旧 Qdrant 向量消失，新数据按新策略生成

## T4 三种检索

- [x] 4.1 keyword：仅查 MySQL 全文索引（class_id = 令牌载荷班级 AND index_status = 'ready'），不调用嵌入、不访问 Qdrant，按全文相关度排序 — verify: 原词检索有命中；停用 Qdrant 后 keyword 仍返回
- [x] 4.2 vector：问句嵌入 → Qdrant 按 class_id 过滤 → 余弦 < 0.35 丢弃 → 主键回 MySQL 取正文（再次核对班级），按相似度排序 — verify: 余弦阈值与班级过滤已实测（原始候选 0.3007/0.0168 均被丢弃；回表按他班班级条件返回空）。注：课程 mock 网关为 bigram 哈希伪向量（非语义），无法构造「同义改写 keyword 落空但 vector 命中」的样例，该子项未能演示。
- [x] 4.3 hybrid：同时执行两路，各路先独立过滤，再按名次 RRF（k=60）融合；缺席一路不贡献分数 — verify: 对同一问句比较 keyword/vector/hybrid 返回次序；两路均命中的切片在 hybrid 中排序更靠前；融合结果不是分数相加
- [x] 4.4 空查询返回 400 — verify: 空问句请求返回 HTTP 400 且不触发嵌入/对话网关
- [x] 4.5 Qdrant 不可用：vector/hybrid 返回 503、不编造相似度分数 — verify: 停用 Qdrant 后 vector/hybrid 为 503，keyword 正常

## T5 检索侧班级隔离

- [x] 5.1 class_id 仅从服务端已校验的令牌身份读取；查询字符串/JSON/Header 中携带的 class_id 解析后丢弃 — verify: A 班 token 请求体写 B 班 class_id，检索结果仍按 A 班过滤
- [x] 5.2 关键字与向量两条路径均带班级条件，回表时再次核对 — verify: 停用 Qdrant 单侧过滤场景下，回表班级约束仍排除他班记录
- [x] 5.3 跨班检索对外表现为无命中（200 空 hits 或固定文案），不以 403/404 暗示资料归属 — verify: A 班检索仅存在于 B 班正文的词 → 200 空 hits；A 班 token 打开 B 班材料详情与不存在同为 404

## T6 问答接口 POST /api/ask

- [x] 6.1 以用户最新一句执行本班混合检索，取前 4 条切片 — verify: 命中 >4 条时只返回 4 条且顺序与 [n] 标注一致
- [x] 6.2 无候选：直接返回「资料中未找到相关内容」、citations 为空、不调用对话网关 — verify: 问天气/比分返回 200 固定文案、citations 空、日志无对话网关调用
- [x] 6.3 有候选：对话模块收到材料标题、切片序号、切片正文、用户提问与此前对话；回答 [1]/[2] 与 citations 顺序一致 — verify: 对材料内问题作答，标注与出处列表顺序一致
- [x] 6.4 客户端注入的 system 消息丢弃；system 由服务端写入，仅允许模型按编号对应资料作答 — verify: 请求体带 system 消息时回答不受其影响
- [x] 6.5 不实现流式输出 — verify: 响应为完整 JSON，非 SSE 流

## T7 规约与收尾

- [x] 7.1 运行 openspec validate add-traceable-vector-retrieval --strict — verify: 命令 exit 0 且无 error
- [x] 7.2 手工对照 spec 关键 Scenario：三种检索、无依据不调用模型、跨班无命中、Qdrant 降级 — verify: 记录每条验收结果（通过/失败）于实验报告
- [ ] 7.3 （可选）两层通过后 openspec archive — verify: 规约合并进 openspec/specs/，change 移入归档目录