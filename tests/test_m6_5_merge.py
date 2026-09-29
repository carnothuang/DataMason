"""M6-5 验收：骨架 + 字段合并为完整 spec（校验失败返回定位清单）。"""
from __future__ import annotations

import pytest

from datamason.auth.service import hash_password
from datamason.models import User

SKELETON = {
    "biz_notes": "客户与订单",
    "tables": [
        {"name": "customers", "biz_name": "客户", "relations": []},
        {"name": "orders", "biz_name": "订单",
         "relations": [{"to_table": "customers"}]},
    ],
}

COLUMNS = {
    "customers": [
        {"name": "id", "type": "INTEGER", "is_pk": True, "autoincrement": True,
         "nullable": False},
        {"name": "name", "biz_name": "姓名", "type": "VARCHAR", "length": 64},
    ],
    "orders": [
        {"name": "id", "type": "INTEGER", "is_pk": True, "autoincrement": True,
         "nullable": False},
        {"name": "customers_id", "type": "INTEGER", "is_fk": True,
         "ref_table": "customers", "ref_column": "id"},
        {"name": "amount", "biz_name": "金额", "type": "DECIMAL"},
    ],
}


@pytest.fixture()
def admin(app, client):
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        s.add(User(username="laomao", display_name="老猫",
                   password_hash=hash_password("pw"), is_platform_admin=True))
        s.commit()
    assert client.post("/api/v1/auth/login",
                       json={"username": "laomao", "password": "pw"}).status_code == 200


def test_merge_ok(app, client, admin):
    resp = client.post("/api/v1/deployments/skeleton/merge",
                       json={"skeleton": SKELETON, "columns": COLUMNS})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["table_count"] == 2
    spec = data["spec_json"]
    tmap = {t["name"]: t for t in spec["tables"]}
    # 归属列系统补齐；主键唯一
    names = {c["name"] for c in tmap["orders"]["columns"]}
    assert {"id", "customers_id", "amount", "created_by", "updated_by"} <= names
    assert sum(1 for c in tmap["orders"]["columns"] if c["is_pk"]) == 1


def test_merge_missing_columns_located(app, client, admin):
    resp = client.post("/api/v1/deployments/skeleton/merge",
                       json={"skeleton": SKELETON,
                             "columns": {"customers": COLUMNS["customers"]}})
    assert resp.status_code == 400
    errs = resp.get_json()["errors"]
    assert any(e["table"] == "orders" and "还没有字段" in e["message"] for e in errs)


def test_merge_relation_without_fk_column(app, client, admin):
    bad = {"customers": COLUMNS["customers"],
           "orders": [COLUMNS["orders"][0], COLUMNS["orders"][2]]}
    resp = client.post("/api/v1/deployments/skeleton/merge",
                       json={"skeleton": SKELETON, "columns": bad})
    assert resp.status_code == 400
    errs = resp.get_json()["errors"]
    assert any(e["table"] == "orders" and "缺少对应的外键列" in e["message"] for e in errs)


def test_merge_invalid_column_located(app, client, admin):
    bad = {"customers": COLUMNS["customers"],
           "orders": COLUMNS["orders"] + [{"name": "x", "type": "MONEY"}]}
    resp = client.post("/api/v1/deployments/skeleton/merge",
                       json={"skeleton": SKELETON, "columns": bad})
    assert resp.status_code == 400
    errs = resp.get_json()["errors"]
    assert any(e["table"] == "orders" and e["column"] == "x" for e in errs)


def test_merge_body_validation(app, client, admin):
    assert client.post("/api/v1/deployments/skeleton/merge",
                       json={"skeleton": SKELETON}).status_code == 400
    assert client.post("/api/v1/deployments/skeleton/merge",
                       json={"columns": COLUMNS}).status_code == 400
