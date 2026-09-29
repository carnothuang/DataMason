"""M9-6 页面布局 schema 与接口（0.15.0 / 技术方案 §六）。

覆盖：页面布局读写/校验、非法区块/表/列/聚合口径拒绝、`"{}"` 默认派生、还原默认清空、
表级 UI 配置读写/列存在性、运行态只读布局合并、stat 聚合与数值列校验、权限（operator 403）。
"""
from __future__ import annotations

import pytest

from datamason.auth.service import hash_password
from datamason.models import User

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
            ],
            "indexes": [],
        },
        {
            "name": "orders",
            "biz_name": "订单",
            "columns": [
                {"name": "id", "biz_name": "主键", "type": "INTEGER",
                 "is_pk": True, "autoincrement": True, "nullable": False},
                {"name": "customer_id", "biz_name": "客户", "type": "INTEGER",
                 "nullable": False, "is_fk": True,
                 "ref_table": "customers", "ref_column": "id"},
                {"name": "amount", "biz_name": "金额", "type": "DECIMAL",
                 "precision": 10, "scale": 2},
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
        "datamason.settings.service.get_default_llm_client",
        lambda _app: FakeLlm(),
    )
    yield


def _deploy(client) -> tuple[int, int]:
    preview = client.post("/api/v1/deployments/preview",
                          json={"description": "测试"}).get_json()
    result = client.post("/api/v1/deployments/confirm", json={
        "spec_json": preview["spec_json"],
        "biz_name": "布局验证",
        "seed_data": False,
    }).get_json()
    did = result["deployment_id"]
    pages = client.get(f"/api/v1/app/{did}").get_json()["pages"]
    crud = next((p for p in pages if p["kind"] == "crud_list"), None)
    return did, crud["id"]


def _layout_payload() -> dict:
    return {
        "version": 1,
        "blocks": [
            {"key": "b1", "x_component": "table",
             "props": {"table": "orders", "columns": ["amount"],
                       "col_widths": {"amount": 160}, "default_view": "table"},
             "layout": {"x": 0, "y": 0, "w": 12, "h": 8}},
            {"key": "b2", "x_component": "stat",
             "props": {"table": "orders", "aggregation": "sum", "column": "amount",
                       "title": "订单总额"},
             "layout": {"x": 0, "y": 8, "w": 6, "h": 4}},
            {"key": "b3", "x_component": "html",
             "props": {"content": "## 说明"}, "layout": {"x": 6, "y": 8, "w": 6, "h": 4}},
        ],
    }


# ---------- 页面布局读写 ----------

def test_page_layout_save_and_read(app, client, admin, patch_llm):
    did, page_id = _deploy(client)
    r = client.put(f"/api/v1/deployments/{did}/pages/{page_id}/layout",
                   json=_layout_payload())
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["blocks"] == 3

    got = client.get(f"/api/v1/deployments/{did}/pages/{page_id}/layout").get_json()
    assert got["degraded"] is False
    assert len(got["blocks"]) == 3
    stat = next(b for b in got["blocks"] if b["x_component"] == "stat")
    assert stat["props"]["aggregation"] == "sum"


def test_page_layout_default_when_empty(app, client, admin, patch_llm):
    did, page_id = _deploy(client)
    got = client.get(f"/api/v1/deployments/{did}/pages/{page_id}/layout").get_json()
    assert got["blocks"] == []
    assert got["degraded"] is False  # "{}" = 未配置，非损坏


def test_page_layout_rejects_unknown_table(app, client, admin, patch_llm):
    did, page_id = _deploy(client)
    payload = _layout_payload()
    payload["blocks"][0]["props"]["table"] = "ghost"
    r = client.put(f"/api/v1/deployments/{did}/pages/{page_id}/layout", json=payload)
    assert r.status_code == 400
    assert "ghost" in r.get_json()["error"]


def test_page_layout_rejects_unknown_column(app, client, admin, patch_llm):
    did, page_id = _deploy(client)
    payload = _layout_payload()
    payload["blocks"][0]["props"]["columns"] = ["nope"]
    r = client.put(f"/api/v1/deployments/{did}/pages/{page_id}/layout", json=payload)
    assert r.status_code == 400
    assert "nope" in r.get_json()["error"]


def test_page_layout_rejects_bad_stat_column(app, client, admin, patch_llm):
    did, page_id = _deploy(client)
    payload = _layout_payload()
    payload["blocks"][1]["props"]["column"] = "nonexistent"
    r = client.put(f"/api/v1/deployments/{did}/pages/{page_id}/layout", json=payload)
    assert r.status_code == 400
    assert "聚合列" in r.get_json()["error"]


def test_page_layout_rejects_unknown_block_type(app, client, admin, patch_llm):
    did, page_id = _deploy(client)
    payload = _layout_payload()
    payload["blocks"][0]["x_component"] = "chart"
    r = client.put(f"/api/v1/deployments/{did}/pages/{page_id}/layout", json=payload)
    assert r.status_code == 400


def test_page_layout_rejects_duplicate_key(app, client, admin, patch_llm):
    did, page_id = _deploy(client)
    payload = _layout_payload()
    payload["blocks"][1]["key"] = "b1"
    r = client.put(f"/api/v1/deployments/{did}/pages/{page_id}/layout", json=payload)
    assert r.status_code == 400


def test_page_layout_reset(app, client, admin, patch_llm):
    did, page_id = _deploy(client)
    client.put(f"/api/v1/deployments/{did}/pages/{page_id}/layout", json=_layout_payload())
    r = client.delete(f"/api/v1/deployments/{did}/pages/{page_id}/layout")
    assert r.status_code == 200
    got = client.get(f"/api/v1/deployments/{did}/pages/{page_id}/layout").get_json()
    assert got["blocks"] == []


# ---------- 表级 UI 配置 ----------

def test_table_ui_config_save_and_read(app, client, admin, patch_llm):
    did, _ = _deploy(client)
    payload = {
        "version": 1,
        "list": {"visible_columns": ["amount", "customer_id"],
                 "col_widths": {"amount": 160}, "default_view": "card",
                 "show_actions": False},
        "detail": {"fields": ["amount", "customer_id"], "columns_per_row": 2},
    }
    r = client.put(f"/api/v1/deployments/{did}/tables/orders/ui-config", json=payload)
    assert r.status_code == 200, r.get_data(as_text=True)
    got = client.get(f"/api/v1/deployments/{did}/tables/orders/ui-config").get_json()
    assert got["list"]["default_view"] == "card"
    assert got["list"]["show_actions"] is False
    assert got["detail"]["columns_per_row"] == 2


def test_table_ui_config_default_when_empty(app, client, admin, patch_llm):
    did, _ = _deploy(client)
    got = client.get(f"/api/v1/deployments/{did}/tables/orders/ui-config").get_json()
    assert got["list"] == {}
    assert got["detail"] == {}


def test_table_ui_config_rejects_unknown_column(app, client, admin, patch_llm):
    did, _ = _deploy(client)
    payload = {"version": 1, "list": {"visible_columns": ["nope"]}}
    r = client.put(f"/api/v1/deployments/{did}/tables/orders/ui-config", json=payload)
    assert r.status_code == 400
    assert "nope" in r.get_json()["error"]


# ---------- 运行态只读布局 ----------

def test_runtime_page_layout_merges_ui_configs(app, client, admin, patch_llm):
    did, page_id = _deploy(client)
    client.put(f"/api/v1/deployments/{did}/pages/{page_id}/layout", json=_layout_payload())
    client.put(f"/api/v1/deployments/{did}/tables/orders/ui-config",
               json={"version": 1, "list": {"default_view": "card"}})
    got = client.get(f"/api/v1/app/{did}/api/pages/{page_id}/layout").get_json()
    assert len(got["blocks"]) == 3
    assert "orders" in got["ui_configs"]
    assert got["ui_configs"]["orders"]["list"]["default_view"] == "card"


# ---------- stat 聚合 ----------

def test_stat_count(app, client, admin, patch_llm):
    did, _ = _deploy(client)
    base = f"/api/v1/app/{did}/api/tables/orders/rows"
    client.post(base, json={"customer_id": 1, "amount": 10})
    client.post(base, json={"customer_id": 1, "amount": 20})
    r = client.get(f"/api/v1/app/{did}/api/tables/orders/stat").get_json()
    assert r["aggregation"] == "count"
    assert r["value"] == 2


def test_stat_sum(app, client, admin, patch_llm):
    did, _ = _deploy(client)
    base = f"/api/v1/app/{did}/api/tables/orders/rows"
    client.post(base, json={"customer_id": 1, "amount": 10})
    client.post(base, json={"customer_id": 1, "amount": 20})
    r = client.get(f"/api/v1/app/{did}/api/tables/orders/stat",
                   query_string={"aggregation": "sum", "column": "amount"}).get_json()
    assert r["value"] == 30


def test_stat_avg(app, client, admin, patch_llm):
    did, _ = _deploy(client)
    base = f"/api/v1/app/{did}/api/tables/orders/rows"
    client.post(base, json={"customer_id": 1, "amount": 10})
    client.post(base, json={"customer_id": 1, "amount": 20})
    r = client.get(f"/api/v1/app/{did}/api/tables/orders/stat",
                   query_string={"aggregation": "avg", "column": "amount"}).get_json()
    assert r["value"] == 15


def test_stat_sum_requires_column(app, client, admin, patch_llm):
    did, _ = _deploy(client)
    r = client.get(f"/api/v1/app/{did}/api/tables/orders/stat",
                   query_string={"aggregation": "sum"})
    assert r.status_code == 400


# ---------- 权限 ----------

def test_operator_cannot_edit_layout(app, client, admin, patch_llm):
    did, page_id = _deploy(client)
    # 建一个 operator 用户并登录
    Session = app.extensions["datamason_session_factory"]
    from datamason.auth.service import hash_password as hp
    from datamason.models import DeploymentMember
    with Session() as s:
        op = User(username="op", display_name="操作员",
                  password_hash=hp("pw"))
        s.add(op)
        s.flush()
        s.add(DeploymentMember(deployment_id=did, user_id=op.id, role="operator"))
        s.commit()
        op_id = op.id
    assert op_id
    opc = app.test_client()
    assert opc.post("/api/v1/auth/login",
                    json={"username": "op", "password": "pw"}).status_code == 200
    r = opc.put(f"/api/v1/deployments/{did}/pages/{page_id}/layout", json=_layout_payload())
    assert r.status_code == 403
    # 运行态只读布局 operator 可读
    got = opc.get(f"/api/v1/app/{did}/api/pages/{page_id}/layout")
    assert got.status_code == 200
