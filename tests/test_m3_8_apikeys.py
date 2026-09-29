"""M3-8 验收：API 开放（DR-11）。

- 部署级 API key：发放（明文仅创建响应回显一次，落库 Fernet 密文、掩码回读）/ 吊销（立即 401）。
- key 认证通道：M2-3 CRUD REST 支持 `X-API-Key` / `Authorization: Bearer`（会话与 key 二选一）；
  key 请求同过列白名单/归属列（记系统 0）/两阶段日志钩子；仅限本部署 CRUD 接口。
- key 管理=创建人独有（DR-14）；key 写操作进 activity_logs 且 actor 标记 key 名。
"""
from __future__ import annotations

import pytest

from datamason.auth.service import hash_password
from datamason.models import ActivityLog, DeploymentApiKey, DeploymentMember, User

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
                {"name": "content", "biz_name": "内容", "type": "TEXT"},
            ],
            "indexes": [],
        }
    ],
}


class FakeLlm:
    def chat_json(self, system_prompt, user_prompt):
        class _Usage:
            tokens_in = 1
            tokens_out = 1
            duration_ms = 1

        return GUESTBOOK_SPEC, _Usage()


@pytest.fixture()
def patch_llm(monkeypatch):
    monkeypatch.setattr(
        "datamason.settings.service.get_default_llm_client",
        lambda _app: FakeLlm(),
    )
    yield


@pytest.fixture()
def env(app):
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        u = User(username="laomao", display_name="老猫",
                 password_hash=hash_password("pw"), is_platform_admin=True)
        s.add(u)
        s.commit()
        laomao_id = u.id
    c = app.test_client()
    assert c.post("/api/v1/auth/login",
                  json={"username": "laomao", "password": "pw"}).status_code == 200
    return {"client": c, "id": laomao_id}


def _mk_user(app, username: str) -> int:
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        u = User(username=username, display_name=username,
                 password_hash=hash_password("pw"))
        s.add(u)
        s.commit()
        return u.id


def _login(app, username: str, password: str = "pw"):
    c = app.test_client()
    assert c.post("/api/v1/auth/login",
                  json={"username": username, "password": password}).status_code == 200
    return c


def _session(app):
    return app.extensions["datamason_session_factory"]()


def _deploy(client, biz_name: str = "留言板") -> int:
    preview = client.post("/api/v1/deployments/preview",
                          json={"description": "留言板"}).get_json()
    return client.post("/api/v1/deployments/confirm", json={
        "spec_json": preview["spec_json"], "biz_name": biz_name, "seed_data": False,
    }).get_json()["deployment_id"]


def test_api_key_issue_use_write_log_revoke(app, env, patch_llm):
    did = _deploy(env["client"])

    # 发放：明文仅此响应回显一次
    resp = env["client"].post(f"/api/v1/deployments/{did}/api-keys", json={"name": "订单同步"})
    assert resp.status_code == 201, resp.get_data(as_text=True)
    body = resp.get_json()
    raw = body["key"]
    assert raw.startswith("dm_") and len(raw) > 20
    assert body["name"] == "订单同步" and body["status"] == "active"

    # 列表：掩码回读，绝不含明文
    listing = env["client"].get(f"/api/v1/deployments/{did}/api-keys").get_json()
    assert len(listing["keys"]) == 1
    k = listing["keys"][0]
    assert k["masked"] == f"{raw[:10]}…" and raw not in str(listing)
    with _session(app) as s:
        row = s.get(DeploymentApiKey, body["id"])
        assert row.key_cipher and raw not in row.key_cipher  # 密文落库

    # 匿名 + 无效 key → 401
    anon = app.test_client()
    assert anon.get(f"/api/v1/app/{did}/api/tables/messages/rows",
                    headers={"X-API-Key": "dm_bogus"}).status_code == 401
    assert anon.get(f"/api/v1/app/{did}/api/tables/messages/rows").status_code == 401

    # 用 key 读真实数据（先由创建人造一条）
    env["client"].post(f"/api/v1/app/{did}/api/tables/messages/rows",
                       json={"visitor": "甲", "content": "hi"})
    got = anon.get(f"/api/v1/app/{did}/api/tables/messages/rows",
                   headers={"X-API-Key": raw})
    assert got.status_code == 200, got.get_data(as_text=True)
    assert any(r["visitor"] == "甲" for r in got.get_json()["data"])

    # 用 key 写：列白名单生效 + 归属列记系统(0) + 日志 actor 标记 key 名
    created = anon.post(f"/api/v1/app/{did}/api/tables/messages/rows",
                        json={"visitor": "外部系统", "content": "sync", "id": 9999},
                        headers={"X-API-Key": raw})
    assert created.status_code == 201, created.get_data(as_text=True)
    pk = created.get_json()["id"]
    assert pk != 9999  # 主键不可被客户端指定
    row = anon.get(f"/api/v1/app/{did}/api/tables/messages/rows/{pk}",
                   headers={"Authorization": f"Bearer {raw}"}).get_json()
    assert row["created_by"] == 0 and row["visitor"] == "外部系统"

    logs = env["client"].get(
        f"/api/v1/app/{did}/api/activity-logs").get_json()["logs"]
    entry = next(lg for lg in logs
                 if lg["action"] == "create" and lg["object_table"] == "messages"
                 and lg["object_pk"] == str(pk))
    assert entry["actor_user_id"] is None
    assert entry["actor_display"] == "API key「订单同步」"

    # 吊销 → 立即 401
    assert env["client"].delete(
        f"/api/v1/deployments/{did}/api-keys/{body['id']}").status_code == 200
    assert anon.get(f"/api/v1/app/{did}/api/tables/messages/rows",
                    headers={"X-API-Key": raw}).status_code == 401
    assert env["client"].get(
        f"/api/v1/deployments/{did}/api-keys").get_json()["keys"][0]["status"] == "revoked"


def test_api_key_scoped_and_creator_only(app, env, patch_llm):
    did_a = _deploy(env["client"], "留言板A")
    did_b = _deploy(env["client"], "留言板B")
    raw = env["client"].post(f"/api/v1/deployments/{did_a}/api-keys",
                             json={"name": "A同步"}).get_json()["key"]

    anon = app.test_client()
    # key 仅限本部署 CRUD：跨部署 → 403；非 CRUD 接口 → 403
    assert anon.get(f"/api/v1/app/{did_b}/api/tables/messages/rows",
                    headers={"X-API-Key": raw}).status_code == 403
    assert anon.get("/api/v1/deployments", headers={"X-API-Key": raw}).status_code == 403

    # key 管理=创建人独有：管理员/操作员 403；非成员创建人 404
    adm_id = _mk_user(app, "adm")
    op_id = _mk_user(app, "op")
    with _session(app) as s:
        s.add(DeploymentMember(deployment_id=did_a, user_id=adm_id, role="admin"))
        s.add(DeploymentMember(deployment_id=did_a, user_id=op_id, role="operator"))
        s.commit()
    assert _login(app, "adm").post(
        f"/api/v1/deployments/{did_a}/api-keys", json={"name": "x"}).status_code == 403
    assert _login(app, "op").post(
        f"/api/v1/deployments/{did_a}/api-keys", json={"name": "x"}).status_code == 403
    assert _login(app, "adm").get(
        f"/api/v1/deployments/{did_a}/api-keys").status_code == 403
    assert app.test_client().get(
        f"/api/v1/deployments/{did_a}/api-keys").status_code == 401

    # 空名 → 400
    assert env["client"].post(
        f"/api/v1/deployments/{did_a}/api-keys", json={"name": ""}).status_code == 400
    # 吊销不存在的 key → 404
    assert env["client"].delete(
        f"/api/v1/deployments/{did_a}/api-keys/999999").status_code == 404