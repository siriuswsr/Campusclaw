"""POST /api/login

① 200 + token + user.role（教师）
② 错密码 / 用户不存在 → 同构 401（无用户枚举）
③ 拿到的 token 可立即用于 /api/me
"""
import os

import requests


def test_login_ok_writes_token(base_url, teacher_creds):
    r = requests.post(f"{base_url}/api/login",
                      json={"username": teacher_creds[0], "password": teacher_creds[1]},
                      timeout=15)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("token"), data
    assert data["user"]["role"] == "teacher", data
    assert "class_id" in data["user"], data

    # 可选写回：ENV_PATH 指向 .env 时追加一行，供外部脚本复用（report/ 已 gitignore）。
    env_path = os.environ.get("ENV_PATH")
    if env_path:
        with open(env_path, "a", encoding="utf-8") as f:
            f.write(f"\nAPI_TOKEN={data['token']}\n")


def test_login_token_works_on_me(base_url, teacher_creds):
    tok = requests.post(f"{base_url}/api/login",
                        json={"username": teacher_creds[0],
                              "password": teacher_creds[1]}, timeout=15).json()["token"]
    r = requests.get(f"{base_url}/api/me",
                     headers={"Authorization": "Bearer " + tok}, timeout=15)
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "teacher", r.text


def test_login_failures_are_identical_401(base_url, teacher_creds):
    bad = requests.post(f"{base_url}/api/login",
                        json={"username": teacher_creds[0], "password": "wrong-on-purpose"},
                        timeout=15)
    ghost = requests.post(f"{base_url}/api/login",
                          json={"username": "no_such_user_xyz", "password": "whatever"},
                          timeout=15)
    assert bad.status_code == 401, bad.text
    assert ghost.status_code == 401, ghost.text
    assert bad.json() == ghost.json(), (bad.json(), ghost.json())
