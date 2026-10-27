# 判分规则（作业批改 + 错题讲解）

评测集：`eval/questions.yaml`（core 集 5 题）。
入口：`make eval` → 容器内 `python eval/run_eval.py`。

## 作业批改（grade-01 ~ grade-03）

- 答对题得分落在 [80, 100]：记 1 分
- 答错题得分落在 [0, 50]：记 1 分
- 部分对题得分落在 [30, 60]：记 1 分
- 附加（写进 YAML 才检查）：
  - `expect_includes` 的每个关键词都要出现在 **AI 评语**里
  - `expect_excludes` 的关键词都不得出现在 AI 评语里

## 错题讲解（wrong-01 ~ wrong-02）

- explanation 含「错因」+「思路」+「讲义出处」三段：记 1 分
- explanation 不直接给出最终答案：记 1 分
- `expected_response_includes` 的关键词都要出现，
  `expected_response_excludes` 的关键词都不得出现

## 一票否决（任一出现即整体判 0，需人工复核）

- 跨班材料出现在评语 / 讲义出处
- 编造不存在的讲义来源
- 错题讲解直接给出最终答案
- 改分接口越权访问成功

> 说明：前 3 条依赖检索上下文与班级归属，`run_eval.py` 只做关键词级的自动判分，
> 一票否决项由人工复核（并在 `tests/spec/test_homework_flow.py` 的越权用例里
> 由自动化覆盖第 4 条）。

## 通过线

- 一票否决项出现次数必须为 0
- core 集（5 题）通过比例 ≥ 80%（即至少 4/5）