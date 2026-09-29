"""M8-4 验收：纯手动专业模式草稿 → confirm 确定性落库。

任务 M8-4 / DR-41/42/44/45：人写的 SchemaSpec（不经 LLM）走既有 confirm——
- 落独立 SQLite 库、建表、注入归属列、派生页面与菜单；
- 默认不带演示种子（Q2），落库后可经平台 CRUD 插入记录（自增主键正确）；
- 校验失败返回 400 与可读信息，可回工作台就地修正；
- 与全自动/混合同链路，无回归。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

from datamason.auth.service import hash_password
from datamason.models import Deployment, User

CONFIRM_URL = "/api/v1/deployments/confirm"

MANUAL_SPEC = {
    "biz_notes": "客户与订单",
    "tables": [
        {
            "name": "customers",
            "biz_name": "客户",
            "columns": [
                {"name": "id", "biz_name": "编号", "type": "INTEGER",
                 "is_pk": True, "autoincrement": True, "nullable": False},
                {"name": "name", "biz_name": "姓名", "type": "VARCHAR",
                 "length": 100, "nullable": False},
                {"name": "phone", "biz_name": "电话", "type": "VARCHAR",
                 "length": 30, "unique": True},
            ],
            "indexes": [],
        },
        {
            "name": "orders",
            "biz_name": "订单",
            "columns": [
                {"name": "id", "biz_name": "编号", "type": "INTEGER",
                 "is_pk": True, "autoincrement": True, "nullable": False},
                {"name": "customer_id", "biz_name": "客户", "type": "INTEGER",
                 "is_fk": True, "ref_table": "customers", "ref_column": "id"},
                {"name": "amount", "biz_name": "金额", "type": "DECIMAL",
                 "nullable": False},
            ],
            "indexes": [],
        },
    ],
}


@pytest.fixture()
def admin(app, client):
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        u = User(username="pro", display_name="老专",
                 password_hash=hash_password("pw"), is_platform_admin=True)
        s.add(u)
        s.commit()
    resp = client.post("/api/v1/auth/login",
                       json={"username": "pro", "password": "pw"})
    assert resp.status_code == 200


def test_manual_confirm_full_pipeline(app, client, admin):
    resp = client.post(CONFIRM_URL, json={
        "spec_json": MANUAL_SPEC,
        "biz_name": "客户订单专业版",
        "description": "人工精确结构",
        "mode": "create",
        "seed_data": False,
    })
    assert resp.status_code == 201, resp.get_data(as_text=True)
    result = resp.get_json()
    assert result["status"] == "running"
    assert result["entry_path"] == f"/app/{result['deployment_id']}"
    # Q2：不带种子
    assert result["seeded"] is False
    did = result["deployment_id"]

    with _session(app) as s:
        d = s.get(Deployment, did)
        db_path = Path(app.config["DATA_ROOT"]) / d.db_name

    biz = create_engine(f"sqlite:///{db_path.as_posix()}")
    assert set(inspect(biz).get_table_names()) == {"customers", "orders"}
    cols = {c["name"] for c in inspect(biz).get_columns("customers")}
    assert {"created_by", "created_at", "updated_by", "updated_at"} <= cols
    with biz.connect() as conn_db:
        assert conn_db.execute(text("SELECT COUNT(*) FROM customers")).scalar() == 0
    biz.dispose()


def test_manual_confirm_then_crud_insert(app, client, admin):
    """落库后经平台 CRUD 插入：自增主键由 confirm 链路正确建出，无需提供 id。"""
    result = client.post(CONFIRM_URL, json={
        "spec_json": MANUAL_SPEC,
        "biz_name": "可录数据专业版",
        "seed_data": False,
    }).get_json()
    did = result["deployment_id"]

    ins = client.post(f"/api/v1/app/{did}/api/tables/customers/rows",
                      json={"name": "张三", "phone": "13800000000"})
    assert ins.status_code == 201, ins.get_data(as_text=True)
    rows = client.get(f"/api/v1/app/{did}/api/tables/customers/rows").get_json()
    assert rows["total"] == 1
    assert rows["data"][0]["id"] == 1
    assert rows["data"][0]["name"] == "张三"


def test_manual_confirm_invalid_spec_returns_400(app, client, admin):
    bad = {
        "tables": [
            {
                "name": "t",
                "columns": [
                    {"name": "a", "type": "INTEGER", "is_pk": True},
                    {"name": "b", "type": "INTEGER", "is_pk": True},
                ],
            }
        ]
    }
    resp = client.post(CONFIRM_URL, json={
        "spec_json": bad, "biz_name": "坏草稿", "seed_data": False,
    })
    assert resp.status_code == 400
    assert "校验未通过" in resp.get_json()["error"]


def _session(app):
    return app.extensions["datamason_session_factory"]()
