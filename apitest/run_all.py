"""一键跑完 apitest 下所有用例，生成 HTML 报告。

    make apitest           # 在 app 容器内执行（封装见仓库 Makefile）
    python apitest/run_all.py

报告路径：apitest/report/report.html（report/ 已在 .gitignore，不进 Git）。
"""
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPORT = HERE / "report" / "report.html"


def main() -> int:
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, "-m", "pytest",
        "cases/", "-v",
        f"--html={REPORT}", "--self-contained-html",
    ]
    return subprocess.call(cmd, cwd=str(HERE))


if __name__ == "__main__":
    raise SystemExit(main())
