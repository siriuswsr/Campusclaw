# 作业流程与三项技能

- 状态: 草稿，2026-10-10
- 覆盖接口: POST/GET /api/assignments, POST /api/submissions,
            PUT /api/submissions/<id>/grade,
            GET /api/wrong, POST /api/wrong/<id>/explain,
            GET /api/assistant/skills（追加 2 个 skill）
- 检查入口: `make check`（pytest tests/spec/test_homework_flow.py）

## ADDED Requirements

### Requirement: 教师 SHALL 可发布本班作业

教师 SHALL 能向自己任教班级发布作业，必填字段：标题、题干、学科、截止时间。

#### Scenario: 教师发布作业成功（正常路径）
- Given 教师 t 已登录，role=teacher，t.class_id=1
- When t POST /api/assignments，
       body={title:"函数单调性练习", body:"...", subject:"math", deadline:"2026-10-15T23:59:00+08:00"}
- Then 状态码 201，响应含 assignment_id
- And 数据库 assignments 新增一行，teacher_id=t.id, class_id=1, status="published"
- And 记一条 audit_log: action="create_assignment", target_id=新 assignment_id

#### Scenario: 教师未指定 class_id（非法输入）
- Given 教师 t 已登录
- When t POST /api/assignments，body 缺 class_id
- Then 状态码 400，message="class_id required"
- And 数据库无变化

#### Scenario: 学生调用发布接口（越权）
- Given 学生 s 已登录，role=student
- When s POST /api/assignments
- Then 状态码 403，message="forbidden: 仅教师可发布作业"
- And 记一条 audit_log: action="create_assignment_forbidden", level=warning

### Requirement: 学生 SHALL 可提交本班作业

学生 SHALL 在截止时间前可提交作答，提交后进入异步批改队列。

#### Scenario: 学生提交作答成功（正常路径）
- Given 学生 s 属于一班，作业 a 由一班教师发布，a.deadline > now
- When s POST /api/submissions，body={assignment_id:a.id, answer_text:"..."}
- Then 状态码 201，响应含 submission_id
- And 数据库 submissions 新增一行，student_id=s.id, status="pending"
- And 异步任务入队：grade_async(s.id, a.id)
- And 记一条 audit_log: action="submit_homework"

#### Scenario: 学生提交他班作业（越权）
- Given 学生 s 属于一班，作业 a 由二班教师发布
- When s POST /api/submissions，body={assignment_id:a.id, ...}
- Then 状态码 404，message="assignment not found"
- And 不创建 submission

#### Scenario: 学生重复提交（业务规则）
- Given 学生 s 已对作业 a 提交过
- When s POST /api/submissions，body={assignment_id:a.id, ...}
- Then 状态码 409，message="already submitted"
- And 不创建新 submission，不重置原状态

#### Scenario: 学生截止后提交（业务规则）
- Given 学生 s 属于一班，作业 a.deadline < now
- When s POST /api/submissions，body={assignment_id:a.id, ...}
- Then 状态码 410，message="deadline passed"
- And 不创建 submission

#### Scenario: 学生作答内容为空（非法输入）
- Given 学生 s 已登录
- When s POST /api/submissions，body={assignment_id:a.id, answer_text:""}
- Then 状态码 400，message="answer_text required"

### Requirement: 助手 SHALL 在异步批改后状态变更为 done

异步批改任务完成后，submission.status SHALL 变更为 done，并写入 AI 分数、AI 评语、引用讲义出处。

#### Scenario: 异步批改成功（正常路径）
- Given 学生 s 已提交作业 a，submissions.status="processing"
- When grade_async 任务完成
- Then submissions.status="done"
- And grades 表新增一行，ai_score ∈ [0, 100], ai_comment 非空, ai_citations 是引用数组
- And 批改若判错（ai_score < 60），自动在 wrong_questions 归集一条
- And 记一条 audit_log: action="ai_grade_done"

#### Scenario: 异步批改调用网关超时（外部依赖失败）
- Given 学生 s 已提交作业 a
- When AI 网关连续超时 2 次
- Then submissions.status="failed"，message="grading failed: timeout"
- And 学生端轮询看到"批改失败，请联系教师"
- And 不写 grades 表

#### Scenario: 异步批改判错自动归集错题
- Given 学生 s 提交作答 a，AI 批改结果 ai_score=55（< 60）
- When grade_async 任务完成
- Then wrong_questions 表新增一行，student_id=s.id, submission_id=a.id,
       question_text=a.body 的前 200 字, status="pending"
- And 错题状态等待学生点击"请助手讲解"

### Requirement: 教师 SHALL 可改分覆盖助手终评

教师 SHALL 对学生提交的批改结果可改分，分数与评语 MUST 覆盖 AI 原值。

#### Scenario: 教师改分成功（正常路径）
- Given 教师 t 属于一班，学生 s 的 submission 来自 t 任教班级的作业，
       submissions.status="done"
- When t PUT /api/submissions/<sid>/grade，body={final_score: 85, final_comment: "..."}
- Then 状态码 200
- And grades.final_score=85, grades.final_comment="...", grades.graded_by=t.id
- And 记一条 audit_log: action="teacher_grade"，含改分前 ai_score 与改分后 final_score

#### Scenario: 教师改他班作业的分（越权）
- Given 教师 t 属于一班，作业 a 由二班教师发布
- When t PUT /api/submissions/<sid>/grade
- Then 状态码 404，message="submission not found"

#### Scenario: 学生调用改分接口（越权）
- Given 学生 s 已登录
- When s PUT /api/submissions/<sid>/grade
- Then 状态码 403，message="forbidden: 仅教师可改分"

### Requirement: 错题讲解技能 SHALL 按错因说明，不直接给答案

调用"错题讲解"技能时，AI MUST 按错因说明（默认 SHALL NOT 直接给最终答案），并引用讲义来源。

#### Scenario: 错题讲解成功（正常路径）
- Given 学生 s 的错题 w.status="pending"
- When s POST /api/wrong/<wid>/explain
- Then 状态码 200，响应含 explanation 字段
- And explanation 含"错因"+"思路"+"讲义出处"三段
- And explanation 不含完整最终答案
- And 记一条 audit_log: action="wrong_explain"

#### Scenario: 错题讲解技能未启用（业务规则）
- Given 班级"错题讲解"技能 enabled=false
- When s POST /api/wrong/<wid>/explain
- Then 状态码 503，message="skill disabled"

#### Scenario: 错题讲解超过他班边界（越权）
- Given 学生 s 属于一班，错题 w 由二班学生产生
- When s POST /api/wrong/<wid>/explain
- Then 状态码 404，message="wrong question not found"

### Requirement: 班级隔离 MUST 严格生效

教师 MUST 只能看到本班作业；学生 MUST 只能看到本班作业与本人提交；错题 MUST 只能本人或本班教师访问。

#### Scenario: 教师跨班查看作业（越权）
- Given 教师 t 属于一班
- When t GET /api/assignments?class_id=2
- Then 状态码 200，results 为空数组

#### Scenario: 学生查看他班作业列表（隔离）
- Given 学生 s 属于一班
- When s GET /api/assignments?class_id=2
- Then 状态码 200，results 为空数组

#### Scenario: 学生跨班查看他人错题（隔离）
- Given 学生 s 属于一班，学生 s2 属于二班
- When s GET /api/wrong/<s2 的错题 id>
- Then 状态码 404

### Requirement: 异步批改 SHALL NOT 阻塞提交

学生提交作业时，POST /api/submissions MUST 在 200ms 内返回，批改在后台线程执行（SHALL NOT 阻塞提交）。

#### Scenario: 提交与批改解耦（性能）
- Given 学生 s 提交作业 a
- When t=0 时刻提交完成
- Then t+200ms 时 submissions.status 可以是 pending 或 processing（不必是 done）
- And 学生 GET /api/submissions/<sid> 持续轮询，状态由 pending → processing → done
- And 批改完成时（< 30s）状态变 done