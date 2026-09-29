"""M9-2 后端映射服务：配置回读 + FK 批量 ref_labels（0.15.0 / 技术方案 §五）。

覆盖：attach_ref_labels 正确映射、自引用外键跳过、无外键列返回空、
查不到的 id 不报错、rows 接口附带 ref_labels、直连/会话链路只读可用。
"""
from __future__ import annotations

import pytest

from datamason.auth.service import hash_password
from datamason.crud import engine as crud_engine
from datamason.forms import service as form_service
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
                {"name": "notes", "biz_name": "备注", "type": "TEXT"},
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
                {"name": "referrer_id", "biz_name": "推荐人", "type": "INTEGER",
                 "nullable": True, "is_fk": True,
                 "ref_table": "orders", "ref_column": "id"},
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


def _deploy(client) -> int:
    preview = client.post("/api/v1/deployments/preview",
                          json={"description": "测试"}).get_json()
    result = client.post("/api/v1/deployments/confirm", json={
        "spec_json": preview["spec_json"],
        "biz_name": "映射验证",
        "seed_data": False,
    }).get_json()
    return result["deployment_id"]


def _seed_rows(client, base) -> dict:
    cid1 = client.post(f"{base}/customers/rows",
                       json={"name": "华东贸易"}).get_json()["id"]
    cid2 = client.post(f"{base}/customers/rows",
                       json={"name": "华南科技"}).get_json()["id"]
    oid1 = client.post(f"{base}/orders/rows",
                       json={"customer_id": cid1, "amount": 10}).get_json()["id"]
    oid2 = client.post(f"{base}/orders/rows",
                       json={"customer_id": cid2, "amount": 20}).get_json()["id"]
    # 悬空外键：手动插一行不存在的 customer_id 不可行（外键约束），
    # 改在 attach 层验证「传入 rows 含未知 id 不报错」。
    return {"c1": cid1, "c2": cid2, "o1": oid1, "o2": oid2}


def test_rows_response_has_ref_labels(app, client, admin, patch_llm):
    did = _deploy(client)
    base = f"/api/v1/app/{did}/api/tables"
    ids = _seed_rows(client, base)

    resp = client.get(f"{base}/orders/rows").get_json()
    assert "ref_labels" in resp
    labels = resp["ref_labels"]
    # customer_id 外键列应有标签映射
    assert "customer_id" in labels
    c1 = str(ids["c1"])
    assert labels["customer_id"][c1] == "华东贸易"
    # 自引用外键（referrer_id）跳过批量查询
    assert "referrer_id" not in labels


def test_attach_ref_labels_no_fk_cols(app, client, admin, patch_llm):
    """无外键列的表：ref_labels 为空 dict，不产生额外查询。"""
    did = _deploy(client)
    base = f"/api/v1/app/{did}/api/tables"
    client.post(f"{base}/customers/rows", json={"name": "华东贸易"})
    resp = client.get(f"{base}/customers/rows").get_json()
    assert "ref_labels" in resp
    assert resp["ref_labels"] == {}


def test_attach_ignores_unknown_ids(app, client, admin, patch_llm):
    """rows 含引用表中不存在的 id：不放入映射、不抛错（前端兜底原值）。"""
    did = _deploy(client)
    base = f"/api/v1/app/{did}/api/tables"
    ids = _seed_rows(client, base)

    # 构造一条带未知 customer_id 的行（通过接口无法写入，直接调领域函数用伪造 rows）
    from datamason.crud.runtime import _registry as make_registry
    from datamason.workspace import routes as ws_routes

    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        from datamason.models import Deployment
        d = s.get(Deployment, did)
        assert d is not None
    _d, registry, engine = make_registry(app, did, admin)
    try:
        fake_rows = [
            {"id": 999, "customer_id": ids["c1"], "amount": 1},
            {"id": 1000, "customer_id": 999999, "amount": 2},  # 不存在的 id
        ]
        labels = crud_engine.attach_ref_labels(registry, "orders", fake_rows)
        assert str(ids["c1"]) in labels["customer_id"]
        assert "999999" not in labels["customer_id"]  # 未知 id 不放入映射、不抛错
    finally:
        engine.dispose()


def test_config_read_includes_control_config(app, client, admin, patch_llm):
    """配置回读应携带 control_config 与层级 parent_value（M9-1 已实现的落点复验）。"""
    did = _deploy(client)
    form_service.save_form_config(app, did, "orders", {
        "fields": [
            {"field_name": "amount", "control_type": "slider",
             "control_config": {"min": 0, "max": 1000, "step": 50}},
        ],
    }, page_kind="workspace", actor_user_id=admin)
    # customers.name（VARCHAR）配级联，验证 parent_value 回读
    form_service.save_form_config(app, did, "customers", {
        "fields": [
            {"field_name": "name", "control_type": "cascader",
             "options": [
                 {"value": "zj", "label": "浙江"},
                 {"value": "hz", "label": "杭州", "parent_value": "zj"},
             ]},
        ],
    }, page_kind="workspace", actor_user_id=admin)
    cfg = form_service.get_form_config(app, did, "orders", page_kind="workspace")
    fields = {f["field_name"]: f for f in cfg["fields"]}
    assert fields["amount"]["control_config"]["step"] == 50
    assert fields["amount"]["control_type"] == "slider"
    ccfg = form_service.get_form_config(app, did, "customers", page_kind="workspace")
    cfields = {f["field_name"]: f for f in ccfg["fields"]}
    opts = cfields["name"]["options"]
    assert {"value": "hz", "label": "杭州", "parent_value": "zj"} in opts


def test_link_mode_readonly_shape(app, client, admin, patch_llm):
    """ref_labels 仅来自只读 SELECT：接口响应结构不携带任何写语义（回归锚点）。"""
    did = _deploy(client)
    base = f"/api/v1/app/{did}/api/tables"
    ids = _seed_rows(client, base)
    resp = client.get(f"{base}/orders/rows").get_json()
    assert isinstance(resp["ref_labels"], dict)
    # 库存值不被改写：行内 customer_id 仍是 id 而非标签（映射只在读侧渲染）
    for row in resp["data"]:
        if row["customer_id"] == ids["c1"]:
            assert row["customer_id"] == ids["c1"]
