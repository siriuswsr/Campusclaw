# 提案：作业流程与三项技能

- change-id: homework-flow
- 提出人: 角色二（开发）
- 日期: 2026-10-10
- 关联 issue: #（仓库内 issue 编号，没建就写 N/A）

## 问题

教学平台需要完整的作业流程：教师发布作业 → 学生截止前提交 →
助手异步批改 → 教师可改分 → 错题进入错题本并由"错题讲解"技能按错因说明。
当前仓库第 5 课已实现"解题引导"技能，作业发布/提交/批改/改分/错题讲解全部缺失；
三项技能中"作业批改"与"错题讲解"也未实现。

## 这次做

1. 教师发布作业（POST /api/assignments），学生提交作答（POST /api/submissions）。
2. 学生提交后进入异步批改队列：pending → processing → done；批改由"作业批改"技能完成。
3. 教师可对助手批改结果改分（PUT /api/submissions/<id>/grade），覆盖终评。
4. 助手判错的题目自动归集到错题本（GET /api/wrong）。
5. 错题讲解技能（POST /api/wrong/<id>/explain）按错因说明（默认不直接给最终答案）。
6. 实现 3 项技能中的 2 项：作业批改（homework_grade）、错题讲解（wrong_answer）；
   解题引导（solve_guide）已在第 5 课完成，仅在本 change 引用。
7. 班级隔离：教师只能管本班作业；学生只能看本班作业与本人提交；错题只能本人或本班教师访问。
8. 关键写入接口挂审计钩子，供第 9 课审计页查询。

## 这次不做

1. 不做作业富文本编辑（仅支持纯文本作答）。
2. 不做作业附件/图片提交。
3. 不做客观题自动判分（仅由"作业批改"技能按 prompt 主观评分）。
4. 不做作业撤回/重交（提交后只能由教师改分）。
5. 不做教师在批改中追加追问对话。
6. 不做消息通知（学生不会收到"作业已批改"提示，需主动刷新页面查）。
7. 不做家长端通知。
8. 不做客观题批改：作业全部是主观题，分数与评语均来自 LLM 评判。

## 边界

- 仅动后端 `app/assignments.py`（新增）、`app/submissions.py`（新增）、
  `app/wrong.py`（新增）、`app/grader.py`（新增）、`app/audit.py`（新增）、
  `app/assistant.py`（追加 2 个技能）、`app/models.py`（追加 4 张表）。
- 前端修改现有 `app/templates/student_home.html`（新增 2 个页面占位）、
  `app/templates/teacher_home.html`（新增 1 个页面占位），把 `base.html` 与 `nav.js`
  中已置灰的 3 个条目（📤 交作业 / ❗ 错题 / 📝 作业与改分）由灰转亮。
- 不改认证、不改 AI 网关调用方式。
- 不引入新依赖：异步队列用 Python `threading` + 内存字典
  （第 8 课再升级为 Celery，本 change 注释里留 TODO）。
- 不写 README/CHANGELOG。