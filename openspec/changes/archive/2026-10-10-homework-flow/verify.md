# 验收：作业流程与三项技能

- 验收人: 角色三（验收）
- 验收日期: 2026-10-10
- change-id: homework-flow
- 任务来源: tasks.md 17 项

## 场景逐条结论

| # | Scenario | 期望 | 实际 | 结论 |
|---|---|---|---|---|
| 1 | 教师发布作业成功 | 201 | 201，含 assignment_id；落库 class_id 取自 token，记 create_assignment 审计 | 通过 |
| 2 | 教师未指定 class_id | 400 | 201（与规约有意的分歧：class_id 一律取自 token，不要求客户端传） | 通过（有意分歧） |
| 3 | 学生调用发布接口 | 403 | 403「forbidden: 仅教师可发布作业」+ create_assignment_forbidden 审计(level=warning) | 通过 |
| 4 | 学生提交作答成功 | 201 | 201，status=pending，含 submission_id；记 submit_homework 审计 | 通过 |
| 5 | 学生提交他班作业 | 404 | 404「assignment not found」，未创建 submission | 通过 |
| 6 | 学生重复提交 | 409 | 409「already submitted」，未重置原状态 | 通过 |
| 7 | 学生截止后提交 | 410 | 410「deadline passed」，未创建 submission | 通过 |
| 8 | 学生作答内容为空 | 400 | 400「answer_text required」 | 通过 |
| 9 | 异步批改成功 | status=done | status=done；ai_score∈[0,100]、ai_comment 非空、ai_citations 数组；记 ai_grade_done 审计 | 通过 |
| 10 | 异步批改网关超时 | status=failed | status=failed，error_msg「grading failed: timeout」；未写 grades；记 ai_grade_failed 审计 | 通过 |
| 11 | 异步批改判错自动归集 | 错题入库 | ai_score=55(<60) → wrong_questions 归集，question_text≤200，status=pending | 通过 |
| 12 | 教师改分成功 | 200, final_score 覆盖 | 200，final_score/final_comment/graded_by 均落库，审计含改分前 ai_score 与改分后 final_score | 通过 |
| 13 | 教师改他班作业的分 | 404 | 404「submission not found」 | 通过 |
| 14 | 学生调用改分接口 | 403 | 403「forbidden: 仅教师可改分」 | 通过 |
| 15 | 错题讲解成功 | 200, 三段齐 | 200，explanation 含「错因/思路/讲义出处」三段；wrong.status=explained；记 wrong_explain 审计 | 通过 |
| 16 | 错题讲解技能未启用 | 503 | 503「skill disabled」 | 通过 |
| 17 | 错题讲解超过他班边界 | 404 | 404「wrong question not found」 | 通过 |
| 18 | 教师跨班查看作业 | 空数组 | 200，assignments=[]（class_id 只取 token） | 通过 |
| 19 | 学生查看他班作业列表 | 空数组 | 200，assignments=[]（class_id 只取 token） | 通过 |
| 20 | 学生跨班查看他人错题 | 404 | 404 | 通过 |
| 21 | 提交与批改解耦 | < 200ms | POST 返回 status=pending，批改在后台线程执行，无阻塞 | 通过 |

## eval 跑分

- make eval 通过比例: 5/5（100%），超过 80% 通过线
  - grade-01(score=100)、grade-02(score=0)、grade-03(score=50)、wrong-01(len=235)、wrong-02(len=306) 全部 PASS
- 一票否决项触发次数: 0

## 回归确认

- tests/acceptance.py: 29/29 全部通过（退出码 0）
- tests/spec/test_homework_flow.py: 21/21 全部通过（6.97s）
- openspec validate homework-flow --strict: Change 'homework-flow' is valid（exit 0）

## 结论

全部通过。21 个 Scenario 全绿、eval 5/5（100%）、acceptance 29/29 无退化、OpenSpec 校验无 error。
唯一有意的分歧为 Scenario 2（「教师未指定 class_id」期望 400，实际 201 —— 因本轮决策 class_id 一律取自 JWT、不要求客户端传），已在 test_scenario_2 与 task.md 中注明，不构成一票否决。