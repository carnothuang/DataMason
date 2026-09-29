"""M2-3 验收：CRUD 引擎完整版（工作台 /app/{id}/api/）。

- POST/PUT/DELETE rows + GET /meta + refs 外键下拉
- 列白名单、参数化、归属列自动填充（会话用户）
- 未登录 401；部署不存在 404
"""
from __future__ import annotations

import pytest

from datamason.auth.service import hash_password
from datamason.models import Deployment, User

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

LINKED_SPEC = {
    "biz_notes": "进销存：客户与订单",
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
def admin(app, client):
    """建管理员并让共享 client 登录。"""
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        u = User(username="laomao", display_name="老猫",
                 password_hash=hash_password("pw"), is_platform_admin=True)
        s.add(u)
        s.commit()
    resp = client.post("/api/v1/auth/login",
                       json={"username": "laomao", "password": "pw"})
    assert resp.status_code == 200
    return u.id


@pytest.fixture()
def patch_llm(monkeypatch):
    monkeypatch.setattr(
        "datamason.settings.service.get_default_llm_client",
        lambda _app: FakeLlm(LINKED_SPEC),
    )
    yield


def _deploy(client, spec_key: str = "linked") -> int:
    preview = client.post("/api/v1/deployments/preview",
                          json={"description": "测试"}).get_json()
    result = client.post("/api/v1/deployments/confirm", json={
        "spec_json": preview["spec_json"],
        "biz_name": "进销存验证",
        "seed_data": False,  # 自动不注入，便于精确断言
    }).get_json()
    return result["deployment_id"]


def _api(prefix: str, name: str):
    return f"/api/v1/app/{prefix}/api/tables/{name}"


def test_crud_lifecycle_via_app_api(app, client, admin, patch_llm):
    did = _deploy(client)
    base = _api(did, "customers")

    # CREATE
    resp = client.post(f"{base}/rows",
                       json={"name": "老猫", "phone": "13900000000"})
    assert resp.status_code == 201, resp.get_data(as_text=True)
    cid = resp.get_json()["id"]

    # READ one（归属列已自动填充）
    one = client.get(f"{base}/rows/{cid}").get_json()
    assert one["name"] == "老猫"
    assert one["created_by"] == admin
    assert one["updated_by"] == admin

    # UPDATE
    upd = client.put(f"{base}/rows/{cid}", json={"phone": "13800000000"})
    assert upd.status_code == 200
    one2 = client.get(f"{base}/rows/{cid}").get_json()
    assert one2["phone"] == "13800000000"
    assert one2["name"] == "老猫"

    # DELETE → 随后 404
    assert client.delete(f"{base}/rows/{cid}").get_json()["deleted"] is True
    assert client.get(f"{base}/rows/{cid}").status_code == 404
    assert client.get(f"{base}/rows/{cid}").get_json()["error"]


def test_ownership_column_takes_session_user(app, client, admin, patch_llm):
    """归属列 created_by 随会话用户变化（M2-3 核心：不再是写死 user_id=1）。"""
    did = _deploy(client)
    base = _api(did, "customers")

    # 第二个普通用户登录后写入
    assert client.post("/api/v1/auth/users", json={
        "username": "xiaoli", "display_name": "小李", "password": "li123",
    }).status_code == 201
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        li_id = s.query(User).filter_by(username="xiaoli").one().id
    # M2-5：成员校验——非成员不能访问工作台
    assert client.post(f"/api/v1/deployments/{did}/members", json={
        "username": "xiaoli", "role": "admin",
    }).status_code == 201
    assert client.post("/api/v1/auth/logout").status_code == 200
    assert client.post("/api/v1/auth/login",
                       json={"username": "xiaoli", "password": "li123"}).status_code == 200

    resp = client.post(f"{base}/rows", json={"name": "小李客户"})
    assert resp.status_code == 201
    cid = resp.get_json()["id"]
    one = client.get(f"{base}/rows/{cid}").get_json()
    assert one["created_by"] == li_id
    assert one["updated_by"] == li_id


def test_write_ignores_unknown_columns(app, client, admin, patch_llm):
    """列白名单：body 里不在反射 schema 的键一律忽略，不落库。"""
    did = _deploy(client)
    base = _api(did, "customers")
    resp = client.post(f"{base}/rows", json={
        "name": "白名单", "evil_col": "想注入的列",
    })
    assert resp.status_code == 201
    cid = resp.get_json()["id"]
    one = client.get(f"{base}/rows/{cid}").get_json()
    assert "evil_col" not in one
    assert one["name"] == "白名单"


def test_read_filter_whitelist_rejected(app, client, admin, patch_llm):
    did = _deploy(client)
    resp = client.get(f"{_api(did, 'customers')}/rows?evil=1")
    assert resp.status_code == 400
    assert "未知过滤列" in resp.get_json()["error"]


def test_unknown_table_404(app, client, admin, patch_llm):
    did = _deploy(client)
    resp = client.get(f"/api/v1/app/{did}/api/tables/ghost/rows")
    assert resp.status_code == 404


def test_meta_includes_columns_and_fk(app, client, admin, patch_llm):
    did = _deploy(client)
    meta = client.get(f"/api/v1/app/{did}/api/meta").get_json()
    assert set(meta) == {"customers", "orders"}
    cols = {c["name"]: c for c in meta["orders"]["columns"]}
    assert cols["customer_id"]["fk"] == {
        "ref_table": "customers", "ref_column": "id",
    }
    assert cols["amount"]["fk"] is None
    intro = {c["name"]: c for c in meta["customers"]["columns"]}
    assert intro["name"]["nullable"] is False
    assert intro["id"]["pk"] is True


def test_refs_fk_dropdown_options_and_search(app, client, admin, patch_llm):
    did = _deploy(client)
    base = _api(did, "customers")
    for i in range(3):
        assert client.post(f"{base}/rows",
                           json={"name": f"客户{i}", "phone": f"1{i}"}).status_code == 201

    # 种子种子里也有 customers/orders 各 0 行（seed_data=False），全量 = 3
    refs = client.get(f"{_api(did, 'orders')}/refs/customer_id").get_json()
    assert refs["ref_table"] == "customers"
    assert refs["ref_column"] == "id"
    assert refs["label_column"] == "name"
    assert refs["total"] == 3
    values = {o["value"] for o in refs["options"]}
    assert values == {1, 2, 3}
    # 标签取业务列而非主键
    assert {o["label"] for o in refs["options"]} == {"客户0", "客户1", "客户2"}

    # search 过滤标签列
    filtered = client.get(
        f"{_api(did, 'orders')}/refs/customer_id?search=客户1"
    ).get_json()
    assert filtered["total"] == 1
    assert filtered["options"][0]["label"] == "客户1"


def test_refs_rejects_unknown_or_non_fk(app, client, admin, patch_llm):
    did = _deploy(client)
    assert client.get(f"{_api(did, 'orders')}/refs/nope").status_code == 400
    resp = client.get(f"{_api(did, 'orders')}/refs/amount")
    assert resp.status_code == 400
    assert "不是外键列" in resp.get_json()["error"]


def test_app_api_requires_login(app, client, admin, patch_llm):
    anon = app.test_client()
    assert anon.get("/api/v1/app/1/api/meta").status_code == 401
    assert anon.post("/api/v1/app/1/api/tables/customers/rows", json={}).status_code == 401


def test_app_api_unknown_deployment(app, client, admin, patch_llm):
    assert client.get("/api/v1/app/999/api/meta").status_code == 404
    assert client.post("/api/v1/app/999/api/tables/customers/rows",
                       json={}).status_code == 404