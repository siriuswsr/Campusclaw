## Purpose

为 CampusClaw 追加可追溯的混合检索：上传成功后对正文做切片与向量嵌入，存入 MySQL 与 Qdrant；学生与教师均可对本班材料按关键字、向量或混合模式检索，并依据命中切片由服务端调用对话模型生成带 [n] 出处标注的简短回答；当本班材料中不存在依据时，固定返回「资料中未找到相关内容」，不得编造出处，亦不得调用对话模型。

## ADDED Requirements

### Requirement: 切分策略

上传成功后，检索模块 MUST 接收 knowledge_entries.body_text 作为切分输入；嵌入模块 MUST NOT 被切分模块触发；切分输出 MUST 写入 knowledge_chunks 表，并保留每条切片在切分输入中的字符位置。MUST 提供 auto、custom、hierarchy 三种策略；未指定策略时按 auto 处理；种子材料启动时补齐索引同样按 auto 处理。

#### Scenario: auto 策略按窗口切分

- **WHEN** 上传一份常规讲义且未指定策略
- **THEN** 切分模块 MUST 按最大 800 字、重叠 80 字的窗口切分
- **AND** 优先在空行、换行、句号处断开
- **AND** 请求中另行填写的长度与预处理参数 MUST NOT 生效

#### Scenario: custom 策略按分隔符切分

- **WHEN** 上传材料并指定 custom 策略
- **THEN** 切分模块 MUST 按用户在请求中给定的分隔符（换行、空行、句号）切分
- **AND** 最大长度 MUST 介于 100 至 2000 字，重叠比例 MUST 介于 0% 至 50%
- **AND** 在无断点处 MUST 按最大长度强制截断
- **AND** 可选的预处理（移除 URL/邮箱、折叠连续空白为单个空格） MUST 仅作用于待切分与待嵌入的文本

#### Scenario: hierarchy 策略按 Markdown 标题分章

- **WHEN** 上传一份含 Markdown 标题的材料并指定 hierarchy 策略
- **THEN** 切分模块 MUST 按 #、##、### 层级分章
- **AND** 标题 MUST 保留在该章切片内
- **AND** 某章超过自动窗口最大长度时 MUST 再按 auto 窗口规则切分

#### Scenario: 切换策略须重建索引

- **WHEN** 教师按新策略对已入库材料发起重建索引
- **THEN** 系统 MUST 先删除旧 knowledge_chunks 记录与旧 Qdrant 向量记录
- **AND** 再按本次策略重新切分、嵌入并写入
- **AND** 已入库材料 MUST NOT 自动重新切分

#### Scenario: 预处理不改写原文

- **WHEN** 切分模块执行预处理（移除 URL/邮箱、折叠空白等）
- **THEN** knowledge_entries.body_text MUST 仍为上传原文
- **AND** 切片偏移量 MUST 是相对于预处理后文本的字符下标，而非原文件下标

### Requirement: 入库流程与数据形态

切片正文 MUST 仅存放于 MySQL knowledge_chunks 表（含 class_id、material_id、knowledge_entry_id、chunk_index、chunk_text、字符偏移等字段），并在该列上建立全文索引。Qdrant MUST 仅保存定长浮点向量，payload 仅含 class_id、material_id、knowledge_entry_id、chunk_id、chunk_index 等标识，MUST NOT 包含 chunk_text。向量主键 MUST 与 knowledge_chunks.id 一致，且 MUST 与 payload.chunk_id 相同。

#### Scenario: 入库四步

- **WHEN** 教师上传一份合法材料且解析成功
- **THEN** 材料模块 MUST 将文件落盘并将正文写入 knowledge_entries.body_text
- **AND** 检索模块 MUST 按本次策略写入 knowledge_chunks
- **AND** 嵌入模块 MUST 将每条切片转换为一条向量
- **AND** 向量主键 MUST 与 knowledge_chunks.id 一致

#### Scenario: 嵌入失败不写不完整向量

- **WHEN** 某条切片的嵌入调用失败
- **THEN** MySQL 中该切片 MUST 标记为 failed 并保留记录
- **AND** Qdrant MUST NOT 写入不完整向量
- **AND** 原上传材料 MUST 仍然保留

#### Scenario: 切片正文仅存于 MySQL

- **WHEN** 任何命中结果被返回给调用方
- **THEN** 摘录 MUST 取自 MySQL 的 chunk_text 字段
- **AND** Qdrant 中 MUST NOT 存在切片正文副本

### Requirement: 三种检索模式

系统 MUST 提供 keyword、vector、hybrid 三种检索模式；默认采用 hybrid。所有模式 MUST 仅访问本站 /api，且 MUST 在服务端完成关键字与向量两条路径的班级过滤。

#### Scenario: keyword 模式仅查 MySQL

- **WHEN** 调用方在请求中指定 mode = keyword
- **THEN** 检索模块 MUST 仅查询 MySQL 全文索引
- **AND** MUST NOT 调用嵌入服务
- **AND** MUST NOT 访问 Qdrant
- **AND** 仅检索 index_status = ready 的切片
- **AND** WHERE 条件 MUST 包含 class_id = 令牌载荷班级
- **AND** 按全文相关度由高到低排序返回

#### Scenario: vector 模式走嵌入与 Qdrant

- **WHEN** 调用方在请求中指定 mode = vector
- **THEN** 检索模块 MUST 先对问句做向量嵌入
- **AND** Qdrant 查询 MUST 按 class_id 过滤
- **AND** 余弦相似度低于 0.35 的候选 MUST 被丢弃
- **AND** 命中后 MUST 以向量主键回 MySQL 取回切片正文
- **AND** 回表查询 MUST 再次核对 class_id = 令牌载荷班级
- **AND** 按余弦相似度由高到低排序返回

#### Scenario: hybrid 模式按名次 RRF 融合

- **WHEN** 调用方在请求中指定 mode = hybrid
- **THEN** 检索模块 MUST 同时执行 keyword 与 vector 两条路径
- **AND** 各路 MUST 先按各自的绝对分数过滤
- **AND** 再按名次执行 RRF（k = 60）融合为最终次序
- **AND** 缺席的一路 MUST NOT 贡献分数
- **AND** 融合结果 MUST NOT 是两路分数直接相加

#### Scenario: 空查询拒绝

- **WHEN** 调用方发送空问句的检索请求
- **THEN** 接口 MUST 返回 HTTP 400
- **AND** MUST NOT 触发嵌入或对话网关调用

### Requirement: 检索侧的班级隔离

班级 MUST 仅从服务端已校验的身份中读取——即 `Authorization: Bearer` 头里的 JWT 载荷（`@require_auth` 解码后注入 `request.user`），MUST NOT 使用任何 Flask session / cookie。请求体、查询字符串或请求头中另行携带的 class_id MUST 在解析后被丢弃；前端隐藏入口 MUST NOT 作为满足本要求的唯一手段。

#### Scenario: 请求体 class_id 被丢弃

- **WHEN** 以 A 班身份登录后，在 JSON 请求体中写入 B 班的 class_id
- **THEN** 系统 MUST 仅使用令牌载荷中的 class_id
- **AND** MySQL 与 Qdrant 两侧的过滤 MUST 均为 A 班
- **AND** 结果中 MUST NOT 出现 B 班的切片

#### Scenario: 两条路径均按令牌身份班级过滤

- **WHEN** 任何检索请求被处理
- **THEN** MySQL 查询条件 MUST 含 class_id = 令牌载荷班级
- **AND** Qdrant 查询 MUST 含 class_id 过滤
- **AND** 仅在单侧过滤 MUST NOT 视为满足隔离（即便向量库漏加过滤条件，回表时的班级约束 MUST 仍能排除他班记录）

#### Scenario: 跨班检索表现为无命中

- **WHEN** A 班用户使用一个仅存在于 B 班正文中的词发起检索
- **THEN** 接口 MUST 返回 HTTP 200 且 hits 为空，或返回「资料中未找到相关内容」
- **AND** MUST NOT 以 HTTP 403 / 404 暗示「该资料属于其他班级」
- **AND** 按材料标识打开详情仍沿用第 3 课规则：跨班与不存在 MUST 均返回 404

### Requirement: 无依据不得凑数

当任一检索模式过滤后无候选切片时，接口 MUST 直接返回「资料中未找到相关内容」，hits 为空；MUST NOT 调用对话网关；MUST NOT 以相关度明显偏低的切片凑数。

#### Scenario: 无命中切片不调用对话模型

- **WHEN** 混合检索取回 0 条切片
- **THEN** 接口 MUST 直接返回固定文案
- **AND** citations MUST 为空
- **AND** 对话网关 MUST NOT 被触发
- **AND** 天气、比分等本班材料不可能出现的问题 MUST 不会被模型以参数记忆勉强作答

#### Scenario: 弱相关候选不得补足

- **WHEN** 候选切片与提问相关度明显偏低（例如提问者用天气问题检索到一份"材料管理"讲义）
- **THEN** 系统 MUST NOT 将其送入对话模块以撑满返回条数
- **AND** MUST 视为无命中并返回固定文案

### Requirement: 问答接口 POST /api/ask

POST /api/ask MUST 仅以用户最新一句问句作为检索输入；MUST 仅以混合模式取本班前 4 条切片；MUST 仅在已有切片的情况下调用对话网关；客户端注入的 system 消息 MUST 被丢弃。

#### Scenario: 仅以本班混合前 4 条切片作答

- **WHEN** 调用方发起 POST /api/ask，本班存在至少 1 条混合命中切片
- **THEN** 检索模块 MUST 仅取前 4 条混合命中的本班切片
- **AND** 对话模块 MUST 收到：材料标题、切片序号、切片正文、用户最新一句提问、以及此前若干轮对话
- **AND** 对话模块 MUST NOT 收到：向量分量、Qdrant 原始点数据、其他班级的切片、客户端自行构造的 system 消息
- **AND** 回答中的 [1]、[2] 标注 MUST 与 citations 出处列表的顺序一致

#### Scenario: 无切片时不调用对话网关

- **WHEN** POST /api/ask 请求中本班混合检索取回 0 条切片
- **THEN** 接口 MUST 直接返回固定文案并附空 citations
- **AND** 对话网关 MUST NOT 被触发
- **AND** 本课 MUST NOT 实现流式输出

#### Scenario: 客户端 system 消息被丢弃

- **WHEN** 调用方在 POST /api/ask 请求体中携带 system 消息
- **THEN** 系统 MUST 丢弃该 system 消息
- **AND** system 提示由服务端在对话调用前写入，且 MUST 仅允许模型依据编号对应的资料作答

### Requirement: 服务端组件边界

浏览器 MUST 仅访问本站 /api（外链前缀 /week04）；Qdrant 与两类模型网关（嵌入网关、对话网关） MUST NOT 暴露为浏览器入口；密钥 MUST 仅保存在服务端。

#### Scenario: 客户端不可直连 Qdrant

- **WHEN** 浏览器尝试直连 Qdrant 端口
- **THEN** 该连接 MUST 被拒绝或不可达
- **AND** 班级过滤与回表查询 MUST 保留在服务端

#### Scenario: 两类模型网关不可被浏览器直接调用

- **WHEN** 浏览器尝试直连嵌入网关或对话网关
- **THEN** 该调用 MUST 不可达
- **AND** 密钥 MUST NOT 下发到浏览器
- **AND** 嵌入与对话 MUST 由服务端检索模块独立发起调用

#### Scenario: 对话模块不可自取材料

- **WHEN** 对话模块被触发以生成回答
- **THEN** 对话模块 MUST NOT 自访问数据库或 Qdrant 检索其他材料
- **AND** 其输入 MUST 由检索模块统一组装并交付

### Requirement: 向量库不可用降级

Qdrant 不可用时，关键字检索 MUST 仍可返回结果；向量与混合检索 MUST 返回 HTTP 503；不编造相似度分数。

#### Scenario: Qdrant 故障时 keyword 仍可用

- **WHEN** Qdrant 服务停止
- **THEN** 调用 mode = keyword 的请求 MUST 仍能成功返回本班关键字命中
- **AND** mode = vector 与 mode = hybrid 的请求 MUST 返回 HTTP 503
- **AND** 接口 MUST NOT 编造相似度分数或混搭关键字与向量结果

#### Scenario: 嵌入服务故障时按路径表现

- **WHEN** 嵌入网关停止调用
- **THEN** mode = keyword MUST 正常返回
- **AND** mode = vector 与 mode = hybrid MUST 返回 HTTP 503
- **AND** 入库流程 MUST 失败已上传材料记录保留，对应切片标记为 failed，不写入不完整向量

#### Scenario: 健康检查与存活

- **WHEN** 部署后任意时刻
- **THEN** GET /health MUST 不经过认证可用，仅用于存活探测
- **AND** GET /health 响应中 MUST NOT 包含任何材料数据、向量或用户信息