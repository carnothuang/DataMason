"""M5-A 验收：结构演化（对自建库增表/删表，0.11.0 DR-27/DR-31）。

- 增表：提案==执行语句；归属列注入；页面/菜单/配置成对生成；重名/悬空外键拒绝
- 删表：影响面（行数/外键引用/页面/菜单/种子）；被引用则阻断；未被引用级联清理+物理 DROP
- 权限：非创建人 403
"""
from __future__ import annotations

import pytest

from server.auth.service import hash_password
from server.models import (
    DeploymentMenu,
    DeploymentSeedRow,
    PageSpec,
    TableSpec,
    User,
)

SPEC = {
    "biz_notes": "进销存：客户与订单",
    "tables": [
        {
            "name": "customers", "biz_name": "客户",
            "columns": [
                {"name": "id", "biz_name": "主键", "type": "INTEGER",
                 "is_pk": True, "autoincrement": True, "nullable": False},
                {"name": "name", "biz_name": "客户名", "type": "VARCHAR",
                 "length": 64, "nullable": False},
            ],
            "indexes": [],
        },
        {
            "name": "orders", "biz_name": "订单",
            "columns": [
                {"name": "id", "biz_name": "主键", "type": "INTEGER",
                 "is_pk": True, "autoincrement": True, "nullable": False},
                {"name": "customer_id", "biz_name": "客户", "type": "INTEGER",
                 "nullable": False, "is_fk": True,
                 "ref_table": "customers", "ref_column": "id"},
                {"name": "amount", "biz_name": "金额", "type": "INTEGER"},
            ],
            "indexes": [],
        },
    ],
}


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


def _mk_user(app, username: str, password: str = "pw") -> int:
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        u = User(username=username, password_hash=hash_password(password))
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
                          json={"description": "进销存"}).get_json()
    return client.post("/api/v1/deployments/confirm", json={
        "spec_json": preview["spec_json"], "biz_name": "进销存", "seed_data": False,
    }).get_json()["deployment_id"]


VISITS = {
    "name": "visits", "biz_name": "回访",
    "columns": [
        {"name": "id", "type": "INTEGER", "is_pk": True, "autoincrement": True, "nullable": False},
        {"name": "customer_id", "type": "INTEGER", "nullable": False,
         "is_fk": True, "ref_table": "customers", "ref_column": "id"},
        {"name": "note", "biz_name": "回访记录", "type": "TEXT"},
    ],
    "indexes": [],
}


def test_add_table_propose_apply_and_use(app, env, patch_llm):
    c = env["client"]
    did = _deploy(c)
    p = f"/api/v1/deployments/{did}"

    prop = c.post(f"{p}/tables/propose", json={"spec": VISITS})
    assert prop.status_code == 200, prop.get_data(as_text=True)
    body = prop.get_json()
    assert body["table"] == "visits"
    # 归属列注入
    names = {col["name"] for col in body["columns"]}
    assert {"created_by", "created_at", "updated_by", "updated_at"} <= names
    assert any("CREATE TABLE" in s for s in body["statements"])

    applied = c.post(f"{p}/tables/apply", json={"spec": VISITS})
    assert applied.status_code == 201, applied.get_data(as_text=True)
    out = applied.get_json()
    assert out["page_id"] and out["menu_id"]

    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        assert s.query(TableSpec).filter_by(deployment_id=did, name="visits").first() is not None
        page = s.get(PageSpec, out["page_id"])
        assert page is not None and page.kind == "crud_list" and page.table_id is not None
        assert s.get(DeploymentMenu, out["menu_id"]).ref_id == out["page_id"]

    # 新表可写可读；外键下拉来自 customers
    created = c.post(f"/api/v1/app/{did}/api/tables/visits/rows",
                     json={"customer_id": 1, "note": "首次回访"})
    assert created.status_code == 201, created.get_data(as_text=True)
    rows = c.get(f"/api/v1/app/{did}/api/tables/visits/rows").get_json()
    assert rows["total"] == 1
    refs = c.get(f"/api/v1/app/{did}/api/tables/visits/refs/customer_id").get_json()
    assert refs["ref_table"] == "customers"

    # 新表默认不在操作员授权集合（可见性约束）：创建人可见
    info = c.get(f"/api/v1/app/{did}").get_json()
    assert any(pg["table"] == "visits" for pg in info["pages"])


def test_add_table_rejects_duplicate_and_dangling_fk(app, env, patch_llm):
    c = env["client"]
    did = _deploy(c)
    p = f"/api/v1/deployments/{did}"

    dup = c.post(f"{p}/tables/propose", json={"spec": VISITS | {"name": "customers"}})
    assert dup.status_code == 400
    assert "已存在" in dup.get_json()["error"]

    dangling = c.post(f"{p}/tables/propose", json={"spec": {
        "name": "bad", "columns": [
            {"name": "id", "type": "INTEGER", "is_pk": True, "nullable": False},
            {"name": "x_id", "type": "INTEGER", "is_fk": True,
             "ref_table": "nope", "ref_column": "id"},
        ],
    }})
    assert dangling.status_code == 400


def test_add_table_requires_creator(app, env, patch_llm):
    c = env["client"]
    did = _deploy(c)
    _mk_user(app, "op")
    # 直接塞一个 operator 成员（无授权）即可验证工程室动作被拒
    from server.models import DeploymentMember
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        uid = s.query(User).filter_by(username="op").first().id
        s.add(DeploymentMember(deployment_id=did, user_id=uid, role="operator"))
        s.commit()
    op = _login(app, "op")
    assert op.post(f"/api/v1/deployments/{did}/tables/propose",
                   json={"spec": VISITS}).status_code == 403


def test_drop_table_blocked_when_referenced(app, env, patch_llm):
    c = env["client"]
    did = _deploy(c)
    p = f"/api/v1/deployments/{did}"
    info = c.post(f"{p}/tables/customers/drop/propose").get_json()
    assert info["blocked"] is True
    assert any(r["table"] == "orders" for r in info["referenced_by"])
    # 确认执行同样被拒
    resp = c.post(f"{p}/tables/customers/drop")
    assert resp.status_code == 400


def test_drop_table_cascade(app, env, patch_llm):
    c = env["client"]
    did = _deploy(c)
    p = f"/api/v1/deployments/{did}"
    assert c.post(f"{p}/tables/apply", json={"spec": VISITS}).status_code == 201
    # 造演示种子登记，验证级联清理
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        s.add(DeploymentSeedRow(deployment_id=did, table_name="visits", row_pk="1"))
        s.commit()

    info = c.post(f"{p}/tables/visits/drop/propose").get_json()
    assert info["blocked"] is False
    assert info["pages"], "应列出关联页面"

    resp = c.post(f"{p}/tables/visits/drop")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    cascade = resp.get_json()["cascade"]
    assert cascade["pages"] >= 1 and cascade["menus"] >= 1 and cascade["seeds"] == 1

    with Session() as s:
        assert s.query(TableSpec).filter_by(deployment_id=did, name="visits").first() is None
        assert s.query(DeploymentSeedRow).filter_by(deployment_id=did, table_name="visits").count() == 0
        # 页面/菜单无悬垂
        assert s.query(PageSpec).filter_by(deployment_id=did, title="回访").count() == 0
    # 工作台 meta 不再含该表
    meta = c.get(f"/api/v1/app/{did}/api/meta").get_json()
    assert "visits" not in meta
