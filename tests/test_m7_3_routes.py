"""M7-3 配置路由与权限（0.13.0 / 技术方案 M7-3）。

覆盖：权限矩阵（创建人/平台管理员可读写、操作员/访客被拒）、操作员越权读他表 403、
错误映射（字段不存在/不相容/候选重复 → 400）、运行态只读缺省返回 200 + degraded。
"""
from __future__ import annotations

import pytest

from server.auth.service import hash_password
from server.models import DeploymentMember, User

SPEC = {
    "biz_notes": "客户与订单",
    "tables": [
        {
            "name": "customers",
            "biz_name": "客户",
            "columns": [
                {"name": "id", "biz_name": "主键", "type": "INTEGER",
                 "is_pk": True, "autoincrement": True, "nullable": False},
                {"name": "name", "biz_name": "客户名", "type": "VARCHAR",
                 "length": 64, "nullable": False},
                {"name": "status", "biz_name": "状态", "type": "VARCHAR", "length": 32},
            ],
            "indexes": [],
        },
    ],
}


class FakeLlm:
    def chat_json(self, system_prompt, user_prompt):
        class _Usage:
            tokens_in = 1
            tokens_out = 1
            duration_ms = 1

        return SPEC, _Usage()


@pytest.fixture()
def patch_llm(monkeypatch):
    monkeypatch.setattr(
        "server.settings.service.get_default_llm_client", lambda _app: FakeLlm())
    yield


def _mk_user(app, username: str, password: str = "pw", admin: bool = False) -> int:
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        u = User(username=username, display_name=username,
                 password_hash=hash_password(password), is_platform_admin=admin)
        s.add(u)
        s.commit()
        return u.id


def _login(app, username, password="pw"):
    c = app.test_client()
    assert c.post("/api/v1/auth/login",
                  json={"username": username, "password": password}).status_code == 200
    return c


def _add_operator(app, deployment_id: int, user_id: int) -> None:
    """直接落成员行（操作员路由要求非空 menu_ids，这里只测配置读取权限）。"""
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        s.add(DeploymentMember(deployment_id=deployment_id, user_id=user_id,
                               role="operator"))
        s.commit()


def _deploy(client) -> int:
    preview = client.post("/api/v1/deployments/preview",
                          json={"description": "测试"}).get_json()
    result = client.post("/api/v1/deployments/confirm", json={
        "spec_json": preview["spec_json"], "biz_name": "表单配置", "seed_data": False,
    }).get_json()
    return result["deployment_id"]


def _payload():
    return {
        "fields": [{"field_name": "status", "control_type": "radio",
                    "options": [{"value": "new", "label": "新建"},
                                {"value": "done", "label": "完成"}]}],
        "layout": {"columns_per_row": 2, "order": [{"field_name": "status"}], "groups": []},
    }


@pytest.fixture()
def creator(app, patch_llm):
    uid = _mk_user(app, "laomao")
    return _login(app, "laomao"), uid


def test_creator_can_read_and_write(app, creator):
    client, _uid = creator
    did = _deploy(client)
    assert client.get(f"/api/v1/deployments/{did}/forms/customers").status_code == 200
    saved = client.put(f"/api/v1/deployments/{did}/forms/customers", json=_payload())
    assert saved.status_code == 200, saved.get_data(as_text=True)
    cfg = client.get(f"/api/v1/deployments/{did}/forms/customers").get_json()
    fields = {f["field_name"]: f for f in cfg["fields"]}
    assert fields["status"]["control_type"] == "radio"


def test_platform_admin_non_member_allowed(app, creator):
    client, _uid = creator
    did = _deploy(client)
    _mk_user(app, "root", admin=True)
    root = _login(app, "root")
    assert root.get(f"/api/v1/deployments/{did}/forms/customers").status_code == 200


def test_operator_cannot_write(app, creator):
    client, _uid = creator
    did = _deploy(client)
    li_id = _mk_user(app, "xiaoli")
    _add_operator(app, did, li_id)
    op = _login(app, "xiaoli")
    assert op.get(f"/api/v1/deployments/{did}/forms/customers").status_code == 403
    assert op.put(f"/api/v1/deployments/{did}/forms/customers",
                  json=_payload()).status_code == 403


def test_operator_runtime_read_unauthorized_table_403(app, creator):
    client, _uid = creator
    did = _deploy(client)
    li_id = _mk_user(app, "xiaoli")
    _add_operator(app, did, li_id)
    op = _login(app, "xiaoli")
    assert op.get(f"/api/v1/app/{did}/api/forms/customers").status_code == 403


def test_anonymous_rejected(app, creator):
    client, _uid = creator
    did = _deploy(client)
    anon = app.test_client()
    assert anon.get(f"/api/v1/deployments/{did}/forms/customers").status_code == 401


def test_runtime_read_missing_config_degraded_200(app, creator):
    client, _uid = creator
    did = _deploy(client)
    resp = client.get(f"/api/v1/app/{did}/api/forms/customers")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["degraded"] is False
    assert {f["field_name"] for f in body["fields"]} >= {"name", "status"}


def test_error_mapping(app, creator):
    client, _uid = creator
    did = _deploy(client)
    # 字段不存在
    r = client.put(f"/api/v1/deployments/{did}/forms/customers",
                   json={"fields": [{"field_name": "ghost", "control_type": "text"}]})
    assert r.status_code == 400
    # 不相容控件
    r = client.put(f"/api/v1/deployments/{did}/forms/customers",
                   json={"fields": [{"field_name": "status", "control_type": "number"}]})
    assert r.status_code == 400
    # 候选项重复
    r = client.put(f"/api/v1/deployments/{did}/forms/customers",
                   json={"fields": [{"field_name": "status", "control_type": "radio",
                                     "options": [{"value": "a", "label": "A"},
                                                 {"value": "a", "label": "B"}]}]})
    assert r.status_code == 400


def test_reset_field_route(app, creator):
    client, _uid = creator
    did = _deploy(client)
    client.put(f"/api/v1/deployments/{did}/forms/customers", json=_payload())
    assert client.delete(
        f"/api/v1/deployments/{did}/forms/customers/fields/status").status_code == 200
    cfg = client.get(f"/api/v1/deployments/{did}/forms/customers").get_json()
    assert {f["field_name"]: f for f in cfg["fields"]}["status"]["configured"] is False
