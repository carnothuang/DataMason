"""M7-2 写前校验回调：三条写路径统一生效（0.13.0 / 技术方案 M7-2）。

覆盖：工作台 / 公开表单 / API key 三条写路径非法载荷被拒、合法载荷通过、
未注入 value_validator（未配置）时行为与升级前一致。
"""
from __future__ import annotations

import pytest

from server.auth.service import hash_password
from server.forms import service as form_service
from server.models import PageSpec, TableSpec, User

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
                {"name": "tags", "biz_name": "标签", "type": "VARCHAR", "length": 255},
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
def admin(app, client):
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        u = User(username="laomao", display_name="老猫",
                 password_hash=hash_password("pw"), is_platform_admin=True)
        s.add(u)
        s.commit()
        uid = u.id
    assert client.post("/api/v1/auth/login",
                       json={"username": "laomao", "password": "pw"}).status_code == 200
    return uid


@pytest.fixture()
def patch_llm(monkeypatch):
    monkeypatch.setattr(
        "server.settings.service.get_default_llm_client",
        lambda _app: FakeLlm(),
    )
    yield


def _deploy(client) -> int:
    preview = client.post("/api/v1/deployments/preview",
                          json={"description": "测试"}).get_json()
    result = client.post("/api/v1/deployments/confirm", json={
        "spec_json": preview["spec_json"],
        "biz_name": "校验验证",
        "seed_data": False,
    }).get_json()
    return result["deployment_id"]


def _config(page_kind: str) -> dict:
    return {
        "fields": [
            {"field_name": "status", "control_type": "radio",
             "options": [{"value": "new", "label": "新建"},
                         {"value": "done", "label": "完成"}]},
            {"field_name": "tags", "control_type": "checkbox",
             "options": [{"value": "a", "label": "A"}, {"value": "b", "label": "B"}]},
        ],
    }


def test_workspace_write_validated(app, client, admin, patch_llm):
    did = _deploy(client)
    form_service.save_form_config(app, did, "customers", _config("workspace"),
                                  page_kind="workspace", actor_user_id=admin)
    base = f"/api/v1/app/{did}/api/tables/customers/rows"

    bad = client.post(base, json={"name": "客户", "status": "unknown"})
    assert bad.status_code == 400
    assert "状态" in bad.get_json()["error"]

    ok = client.post(base, json={"name": "客户", "status": "new",
                                 "tags": ["a", "b"]})
    assert ok.status_code == 201, ok.get_data(as_text=True)
    cid = ok.get_json()["id"]
    row = client.get(f"{base}/{cid}").get_json()
    assert row["status"] == "new"
    assert row["tags"] == '["a", "b"]'  # 多选序列化为 JSON 串落库


def test_checkbox_non_array_rejected(app, client, admin, patch_llm):
    did = _deploy(client)
    form_service.save_form_config(app, did, "customers", _config("workspace"),
                                  page_kind="workspace", actor_user_id=admin)
    base = f"/api/v1/app/{did}/api/tables/customers/rows"
    bad = client.post(base, json={"name": "客户", "tags": "a"})
    assert bad.status_code == 400
    assert "数组" in bad.get_json()["error"]


def test_public_write_validated(app, client, admin, patch_llm):
    did = _deploy(client)
    form_service.save_form_config(app, did, "customers", _config("public"),
                                  page_kind="public", actor_user_id=admin)
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        ts = s.query(TableSpec).filter_by(deployment_id=did, name="customers").first()
        page = PageSpec(deployment_id=did, kind="public_form", access="public",
                        table_id=ts.id, title="客户登记")
        s.add(page)
        s.commit()
        page_id = page.id

    anon = app.test_client()
    bad = anon.post(f"/api/v1/public/{did}/pages/{page_id}/submit",
                    json={"name": "访客", "status": "unknown"})
    assert bad.status_code == 400
    ok = anon.post(f"/api/v1/public/{did}/pages/{page_id}/submit",
                   json={"name": "访客", "status": "done"})
    assert ok.status_code == 201, ok.get_data(as_text=True)


def test_api_key_write_validated(app, client, admin, patch_llm):
    did = _deploy(client)
    form_service.save_form_config(app, did, "customers", _config("workspace"),
                                  page_kind="workspace", actor_user_id=admin)
    key = client.post(f"/api/v1/deployments/{did}/api-keys",
                      json={"name": "外部同步"}).get_json()["key"]

    ext = app.test_client()
    base = f"/api/v1/app/{did}/api/tables/customers/rows"
    bad = ext.post(base, json={"name": "外部", "status": "unknown"},
                   headers={"X-API-Key": key})
    assert bad.status_code == 400
    ok = ext.post(base, json={"name": "外部", "status": "new"},
                  headers={"X-API-Key": key})
    assert ok.status_code == 201, ok.get_data(as_text=True)


def test_unconfigured_behaviour_unchanged(app, client, admin, patch_llm):
    """未配置表单控件的表：任意值均放行（与升级前一致）。"""
    did = _deploy(client)
    base = f"/api/v1/app/{did}/api/tables/customers/rows"
    ok = client.post(base, json={"name": "客户", "status": "任意值"})
    assert ok.status_code == 201
