# 设计：作业流程与三项技能

- 状态: 已定，2026-10-10

## 决定

1. **异步批改用 Python `threading.Thread` + 内存字典 `_GRADING_JOBS`** 模拟队列，
   不引入 Celery。代码注释里留 `# TODO(week-08): replace with Celery + Redis`。
2. **submission 状态机** 固定为 `pending → processing → done`，
   失败转 `failed`；不在中间塞其他状态。
3. **错题归集在批改任务完成时同步触发**，不在批改任务外另起钩子，
   保证"AI 判错 → 错题一定入库"是同一个原子操作。
4. **改分覆盖 AI 终评** 而非追加：final_score 字段直接覆盖 ai_score，
   audit_log 留原 AI 值与改分后值以便追溯。
5. **3 项技能统一在 assistant_skills 表** 通过 skill_name 区分
   （solve_guide / homework_grade / wrong_answer），通过 enabled 字段控制启停。
6. **所有写入接口挂审计钩子**，由 `app/audit.py` 提供 `record_audit(action, target_id, **fields)`，
   调用方传 action 与 target_id，第 9 课直接查这张表。

## 为什么

1. **线程而不是 Celery**：第 7 课先跑通业务正确性，"提交后 5 秒看到 done"足够；
   Celery/Redis 在第 8 课才正式讲，过早引入会把课程节奏打乱。
2. **3 状态而非 5 状态**：需求方只关心"等不等得到结果"与"结果对不对"，
   多状态会让学生端 UI 复杂化；保留 failed 是为了排查超时。
3. **错题归集与批改任务绑定**：分开会有"AI 判错但归集失败"的中间态，
   让错题本少一条，验收时说不清。
4. **覆盖而非追加**：教师改分是终局动作，追加会让学生看到两份分；
   audit_log 留双值足够追溯。
5. **统一 skill_name**：第 5 课已用 skill_name 作为技能主键，
   本次新增两个 skill_name 不破坏既有结构。
6. **审计钩子集中**：第 9 课的"审计与统计"页要按 action 筛，
   钩子集中可以保证字段一致。

## 舍弃的方案

1. **作业批改用 OpenAI function calling 抽题目结构**：题目是主观题，
   function calling 不适用；继续用 prompt 注入。
2. **改分可撤回**：教师一旦覆盖 AI 终评就不可撤回，简化权限模型；
   "撤销改分"放在第 9 课之后的扩展 change。
3. **错题由学生手动"加入错题本"**：会增加学生操作负担；
   按 AI 判错自动归集更一致。
4. **批改任务用 asyncio**：当前 Flask 是同步框架，
   引入 asyncio 需要重写；用 threading 不影响主流程。
5. **错题讲解技能直接给答案**：课程要求"按错因说明（默认不直接给答案）"，
   违反即 eval 的一票否决项。