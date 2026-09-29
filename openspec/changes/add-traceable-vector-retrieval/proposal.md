第 3 课已完成身份、角色与班级隔离，教师上传的教研材料正文已入库（knowledge_entries.body_text）。但材料只能整篇查看，学生无法快速定位自己需要的片段；直接问模型又无法回溯到本班讲义。本变更在既有系统上增加可追溯的知识库检索：以一句自然语言在本班材料中定位相关片段，并标注其来自哪份材料的哪一个切片；当资料中不存在依据时，明确返回未找到，不编造出处。
What Changes
切分：上传成功后（沿用第 3 课材料入库），将正文 body_text 按策略切分为切片写入 knowledge_chunks 表；提供 auto / custom / hierarchy 三种策略；未指定策略时按 auto 处理。
嵌入与入库：每个切片调用嵌入网关转换为固定维度浮点向量；向量写入 Qdrant 集合 campusclaw_chunks（度量余弦），向量主键 = knowledge_chunks.id；payload 仅含若干标识、不含正文。
三种检索：keyword（仅 MySQL 全文索引）、vector（问句嵌入 → Qdrant 按班过滤 → 余弦阈值 0.35 → 回表取正文）、hybrid（两路独立过滤后按名次 RRF k=60 融合）；默认 hybrid。
检索侧班级隔离：class_id 仅从会话读取；关键字与向量两条路径都带班级条件，回表再核对；跨班检索对外表现为无命中。
问答接口 POST /api/ask：本班混合检索取前 4 条切片；有命中才调用对话网关生成带 [n] 出处的简短回答；无命中直接返回「资料中未找到相关内容」，不调用生成模型。
降级与健康：Qdrant 不可用时 keyword 仍可用，vector/hybrid 返回 503；空查询返回 400。
Capabilities
New Capabilities
knowledge-retrieval：正文切分与向量入库、关键字 / 向量 / 混合三种检索、检索侧班级隔离、POST /api/ask 有依据才生成的问答与出处标注、Qdrant 不可用时的降级行为。
Modified Capabilities
auth-upload（第 3 课已归档）：上传入库流程追加「切分 → 嵌入 → 写 Qdrant」步骤，但登录、角色、隔离、上传事务等既有行为不变。
Impact
数据库：新增 knowledge_chunks 表（含 class_id、material_id、knowledge_entry_id、chunk_index、chunk_text、字符偏移、index_status），并在 chunk_text 上建 MySQL 全文索引（WITH PARSER ngram，token 长度 2）。
向量库：Compose 新增 Qdrant 服务（campusclaw_chunks 集合，度量余弦，不向宿主机映射端口）。
服务端：新增切分模块、嵌入模块、检索模块、对话模块；调用课程网关（兼容 OpenAI /embeddings 与对话接口），密钥仅服务端环境变量。
可运行实现按 tasks.md 在 Apply 阶段完成；本 change 交付物为四件套规约（若课程提供原文，以原文为准）。
Non-goals（非目标）
本 change 明确不做以下内容：
流式输出与多轮长对话：第 5 课内容，本课不实现流式。
重排序与编排框架：不引入交叉编码器重排序；不引入 LangChain / LlamaIndex / Dify 等编排框架。
向量库暴露给浏览器：Qdrant 与两类模型网关均不作为浏览器入口。
全文检索替代：不将全文索引并入单一检索引擎（如 ES），班级过滤与事务一致性仍沿用 MySQL + Qdrant 两库设计。
前端替代服务端隔离：不以隐藏入口作为班级隔离的唯一手段。