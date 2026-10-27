# CampusClaw — 规约测试与评测入口
#
# 两个目标都在 app 容器内执行：MySQL / Qdrant 只在 compose 网络内可达
# （宿主机的 3306 / 6333 没有端口映射），而且只有进程内才能 monkeypatch
# 批改网关，所以 pytest 不能跑在宿主机上。
#
# 用法：
#   docker compose up -d
#   make check
#   make eval
#   make apitest

.PHONY: check eval apitest

check:  ## pytest 全套（tests/spec/test_homework_flow.py 的 21 个 Scenario）
	docker compose exec -T app pytest tests/ -v

eval:   ## 作业批改 + 错题讲解评测（真实课程网关，5 题）
	docker compose exec -T app python eval/run_eval.py

apitest:  ## 接口一键跑测（apitest/，pytest + pytest-html → apitest/report/report.html）
	docker compose exec -T app python apitest/run_all.py
