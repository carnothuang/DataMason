"""M5-C 验收：操作权限 + 数据权限（0.11.0，DR-30）。

- 操作动作可配置：无 delete/export 权的操作员直调对应接口 → 403
- 数据范围可配置：data_scope=all 的操作员可见他人记录；默认 own 只见自己
- 空配置 = 角色默认（存量兼容）：可增改删自己的、可导出
- 工作台载荷下发每页 actions/data_scope；meta 下发每表 actions
- API key 为服务身份，不受操作/数据权限限制
"""
from __future__ import annotations

import pytest

from server.auth.service import hash_password
from server.models import DeploymentMenu, User

SPEC = {
    "biz_notes": "客户",
    "tables": [
        {
            "name": "customers",
            "biz_name": "客户",
            "columns": [
                {"name": "id", "biz_name": "主键", "type": "INTEGER",
                 "is_pk": True, "autoincrement": True, "nullable": False},
                {"name": "name", "biz_name": "客户名", "type": "VARCHAR",
                 "length": 64, "nullable": False},
                {"name": "phone", "biz_name": "电话", "type": "VARCHAR", "length": 32},
            ],
            "indexes": [],
        }
    ],
}

ALL_ACTIONS = {"view": True, "create": True, "update": True,
               "delete": True, "export": True, "import": True}


class FakeLlm:
    def __init__(self, payload):
        self.payload = payload

    def chat_json(self, system_prompt, user_prompt):
        class _Usage:
            tokens_in = 1
            tokens_out = 1
            duration_ms = 1

        return self.payload, _Usage()


@pytest.fixture()
def patch_llm(monkeypatch):
    monkeypatch.setattr(
        "server.settings.service.get_default_llm_client",
        lambda _app: FakeLlm(SPEC),
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
    c = app.test_client()
    assert c.post("/api/v1/auth/login",
                  json={"username": "laomao", "password": "pw"}).status_code == 200
    return {"client": c}


def _mk_user(app, username: str, password: str = "pw", display: str = "") -> int:
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        u = User(username=username, display_name=display or username,
                 password_hash=hash_password(password))
        s.add(u)
        s.commit()
        return u.id


def _login(app, username: str, password: str = "pw"):
    c = app.test_client()
    assert c.post("/api/v1/auth/login",
                  json={"username": username, "password": password}).status_code == 200
    return c


def _deploy(client) -> int:
    preview = client.post("/api/v1/deployments/preview",
                          json={"description": "客户"}).get_json()
    result = client.post("/api/v1/deployments/confirm", json={
        "spec_json": preview["spec_json"], "biz_name": "客户", "seed_data": False,
    }).get_json()
    return result["deployment_id"]


def _menu_id(app, did: int) -> int:
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        m = (s.query(DeploymentMenu).filter_by(deployment_id=did)
             .order_by(DeploymentMenu.id).first())
        return m.id


def _api(did: int, name: str = "customers") -> str:
    return f"/api/v1/app/{did}/api/tables/{name}"


def _grant(client, did: int, username: str, menu_id: int,
           actions: dict | None = None, scope: str | None = None):
    payload = {"username": username, "role": "operator", "menu_ids": [menu_id]}
    if actions is not None:
        payload["actions"] = {str(menu_id): actions}
    if scope is not None:
        payload["data_scope"] = {str(menu_id): scope}
    resp = client.post(f"/api/v1/deployments/{did}/members", json=payload)
    assert resp.status_code == 201, resp.get_data(as_text=True)


def _limited_actions(allow: set[str]) -> dict:
    return {k: (k in allow) for k in ALL_ACTIONS}


# ---------------- 操作权限 ----------------

def test_operator_action_permissions_enforced(app, env, patch_llm):
    c = env["client"]
    did = _deploy(c)
    menu = _menu_id(app, did)
    _mk_user(app, "xiaoli", display="小李")
    # 只能看 + 新增
    _grant(c, did, "xiaoli", menu, actions=_limited_actions({"view", "create"}), scope="own")
    op = _login(app, "xiaoli")

    created = op.post(f"{_api(did)}/rows", json={"name": "小李客户"})
    assert created.status_code == 201
    pk = created.get_json()["id"]

    # 无 update/delete/export 权限 → 403（不是 404，动作级拒绝）
    assert op.put(f"{_api(did)}/rows/{pk}", json={"phone": "1"}).status_code == 403
    assert op.delete(f"{_api(did)}/rows/{pk}").status_code == 403
    assert op.get(f"{_api(did)}/rows.csv").status_code == 403
    # view 仍可用
    assert op.get(f"{_api(did)}/rows").status_code == 200


def test_operator_export_granted(app, env, patch_llm):
    c = env["client"]
    did = _deploy(c)
    menu = _menu_id(app, did)
    _mk_user(app, "xiaoli", display="小李")
    _grant(c, did, "xiaoli", menu,
           actions=_limited_actions({"view", "export"}), scope="own")
    op = _login(app, "xiaoli")
    assert op.get(f"{_api(did)}/rows.csv").status_code == 200
    # 无 create
    assert op.post(f"{_api(did)}/rows", json={"name": "x"}).status_code == 403


def test_role_default_compat_when_actions_empty(app, env, patch_llm):
    """空配置 = 角色默认（可增改删自己的、可导出），保证升级不改变存量行为。"""
    c = env["client"]
    did = _deploy(c)
    menu = _menu_id(app, did)
    _mk_user(app, "xiaoli", display="小李")
    _grant(c, did, "xiaoli", menu)  # 不带 actions/data_scope
    op = _login(app, "xiaoli")
    created = op.post(f"{_api(did)}/rows", json={"name": "小李客户"})
    assert created.status_code == 201
    pk = created.get_json()["id"]
    assert op.put(f"{_api(did)}/rows/{pk}", json={"phone": "2"}).status_code == 200
    assert op.get(f"{_api(did)}/rows.csv").status_code == 200
    assert op.delete(f"{_api(did)}/rows/{pk}").status_code == 200


# ---------------- 数据权限 ----------------

def test_data_scope_all_operator_sees_everything(app, env, patch_llm):
    c = env["client"]
    did = _deploy(c)
    menu = _menu_id(app, did)
    _mk_user(app, "xiaoli", display="小李")
    _grant(c, did, "xiaoli", menu, actions=_limited_actions({"view"}), scope="all")
    op = _login(app, "xiaoli")

    other = c.post(f"{_api(did)}/rows", json={"name": "老猫客户"})
    assert other.status_code == 201
    mine = op.post(f"{_api(did)}/rows", json={"name": "小李客户"})
    assert mine.status_code == 403  # 该配置无 create 权

    rows = op.get(f"{_api(did)}/rows?page_size=50").get_json()
    assert rows["total"] == 1
    assert rows["data"][0]["name"] == "老猫客户"  # scope=all 可见他人记录


def test_data_scope_default_own(app, env, patch_llm):
    c = env["client"]
    did = _deploy(c)
    menu = _menu_id(app, did)
    _mk_user(app, "xiaoli", display="小李")
    _grant(c, did, "xiaoli", menu, actions=_limited_actions({"view", "create"}), scope="own")
    op = _login(app, "xiaoli")
    c.post(f"{_api(did)}/rows", json={"name": "老猫客户"})
    op.post(f"{_api(did)}/rows", json={"name": "小李客户"})
    rows = op.get(f"{_api(did)}/rows?page_size=50").get_json()
    assert rows["total"] == 1
    assert rows["data"][0]["name"] == "小李客户"


# ---------------- 下发 ----------------

def test_workspace_payload_carries_actions(app, env, patch_llm):
    c = env["client"]
    did = _deploy(c)
    menu = _menu_id(app, did)
    _mk_user(app, "xiaoli", display="小李")
    _grant(c, did, "xiaoli", menu,
           actions=_limited_actions({"view", "create"}), scope="all")
    op = _login(app, "xiaoli")
    info = op.get(f"/api/v1/app/{did}").get_json()
    page = info["pages"][0]
    assert page["actions"] == ["create", "view"]
    assert page["data_scope"] == "all"
    meta = op.get(f"/api/v1/app/{did}/api/meta").get_json()
    assert meta["customers"]["actions"] == ["create", "view"]


# ---------------- 服务身份不受限 ----------------

def test_api_key_service_identity_bypasses_action_perms(app, env, patch_llm):
    c = env["client"]
    did = _deploy(c)
    menu = _menu_id(app, did)
    _mk_user(app, "xiaoli", display="小李")
    _grant(c, did, "xiaoli", menu, actions=_limited_actions({"view"}), scope="own")

    # 创建人发放 key
    created = c.post(f"/api/v1/deployments/{did}/api-keys", json={"name": "同步"})
    assert created.status_code == 201, created.get_data(as_text=True)
    key = created.get_json()["key"]
    headers = {"X-API-Key": key}

    # key 是服务身份：可写（不受操作权限限制）
    resp = c.post(f"{_api(did)}/rows", json={"name": "key客户"}, headers=headers)
    assert resp.status_code == 201, resp.get_data(as_text=True)
    pk = resp.get_json()["id"]
    assert c.delete(f"{_api(did)}/rows/{pk}", headers=headers).status_code == 200
    assert c.get(f"{_api(did)}/rows.csv", headers=headers).status_code == 200
