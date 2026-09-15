"""M2-1 验收：认证与会话。

- 登录/登出/me + 白名单放行
- 未登录业务接口 401；非管理员访问设置/账号管理 403
- 部署归属（owner_id）与种子归属列取会话用户
- 账号管理：建用户/改密/重名拒绝
"""
from __future__ import annotations

import pytest

from server.auth.service import hash_password
from server.models import Deployment, User

GUESTBOOK_SPEC = {
    "biz_notes": "访客留言",
    "tables": [
        {
            "name": "messages",
            "biz_name": "留言",
            "columns": [
                {"name": "id", "biz_name": "主键", "type": "INTEGER",
                 "is_pk": True, "autoincrement": True, "nullable": False},
                {"name": "visitor", "biz_name": "访客", "type": "VARCHAR",
                 "length": 64, "nullable": False},
            ],
            "indexes": [],
        }
    ],
}


class FakeLlm:
    def chat_json(self, system_prompt, user_prompt):
        class _Usage:
            tokens_in = tokens_out = duration_ms = 1

        return GUESTBOOK_SPEC, _Usage()


@pytest.fixture()
def admin(app):
    with app.extensions["datamason_session_factory"]() as s:
        u = User(username="laomao", display_name="老猫",
                 password_hash=hash_password("pw"), is_platform_admin=True)
        s.add(u)
        s.commit()
        return u.id


@pytest.fixture()
def patch_llm(monkeypatch):
    monkeypatch.setattr(
        "server.settings.service.get_default_llm_client",
        lambda _app: FakeLlm(),
    )
    yield


def _login(client, username="laomao", password="pw"):
    return client.post("/api/v1/auth/login",
                       json={"username": username, "password": password})


# ---------------- 登录 / 会话 ----------------


def test_login_success_and_me(app, client, admin):
    resp = _login(client)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["username"] == "laomao" and body["is_platform_admin"] is True

    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.get_json()["id"] == admin


def test_login_wrong_password(app, client, admin):
    resp = _login(client, password="wrong")
    assert resp.status_code == 401
    assert "用户名或密码错误" in resp.get_json()["error"]


def test_login_missing_fields(app, client):
    assert _login(client, username="").status_code == 400
    assert _login(client, password="").status_code == 400


def test_logout_clears_session(app, client, admin):
    assert _login(client).status_code == 200
    assert client.post("/api/v1/auth/logout").status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.get("/api/v1/deployments").status_code == 401


# ---------------- 拦截层 ----------------


def test_public_endpoints_still_open(app, client):
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/auth/setup-status").status_code == 200
    assert client.post("/api/v1/auth/bootstrap").status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401  # 可达但未登录


def test_protected_endpoints_require_login(app, client):
    assert client.get("/api/v1/deployments").status_code == 401
    assert client.get("/api/v1/settings/models").status_code == 401
    assert client.get("/api/v1/auth/users").status_code == 401


def test_non_admin_forbidden_on_settings_and_users(app, client, admin, patch_llm):
    # 建一个普通用户并登录
    assert _login(client).status_code == 200
    resp = client.post("/api/v1/auth/users", json={
        "username": "xiaoli", "display_name": "小李", "password": "li123",
    })
    assert resp.status_code == 201
    li_id = resp.get_json()["id"]

    # 换小李登录
    assert client.post("/api/v1/auth/logout").status_code == 200
    assert _login(client, "xiaoli", "li123").status_code == 200

    # 普通用户：设置/账号管理 403；部署业务接口放行
    assert client.get("/api/v1/settings/models").status_code == 403
    assert client.get("/api/v1/auth/users").status_code == 403
    assert client.get("/api/v1/deployments").status_code == 200

    # 小李部署 → owner 是小李（会话注入）
    preview = client.post("/api/v1/deployments/preview",
                          json={"description": "留言板"}).get_json()
    result = client.post("/api/v1/deployments/confirm", json={
        "spec_json": preview["spec_json"], "biz_name": "小李的系统",
        "seed_data": True,
    }).get_json()
    with app.extensions["datamason_session_factory"]() as s:
        assert s.get(Deployment, result["deployment_id"]).owner_id == li_id


# ---------------- 账号管理 ----------------


def test_user_management_crud(app, client, admin):
    assert _login(client).status_code == 200

    # 建
    created = client.post("/api/v1/auth/users", json={
        "username": "wangjie", "display_name": "王姐", "password": "jie123",
        "is_platform_admin": True,
    })
    assert created.status_code == 201
    uid = created.get_json()["id"]

    # 重名拒绝
    dup = client.post("/api/v1/auth/users", json={
        "username": "wangjie", "password": "x12345",
    })
    assert dup.status_code == 400

    # 列表
    users = client.get("/api/v1/auth/users").get_json()
    assert {u["username"] for u in users} >= {"laomao", "wangjie"}
    assert all("password" not in u and "password_hash" not in u for u in users)

    # 改密 + 改显示名
    upd = client.put(f"/api/v1/auth/users/{uid}", json={
        "display_name": "王姐2", "password": "newpw9",
    })
    assert upd.status_code == 200

    # 新密可登录，旧密不行
    assert client.post("/api/v1/auth/logout").status_code == 200
    assert _login(client, "wangjie", "jie123").status_code == 401
    assert _login(client, "wangjie", "newpw9").status_code == 200

    # 改回管理员自己之外的不存在用户
    assert client.put("/api/v1/auth/users/999",
                      json={"display_name": "x"}).status_code == 404


def test_owner_takes_session_user(app, client, admin, patch_llm):
    """部署归属 = 会话用户（M2-1 核心验收：替换'取首个管理员'占位）。"""
    assert _login(client).status_code == 200
    preview = client.post("/api/v1/deployments/preview",
                          json={"description": "留言板"}).get_json()
    result = client.post("/api/v1/deployments/confirm", json={
        "spec_json": preview["spec_json"], "biz_name": "归属验证",
        "seed_data": True,
    }).get_json()
    with app.extensions["datamason_session_factory"]() as s:
        assert s.get(Deployment, result["deployment_id"]).owner_id == admin
