"""M2-4 后端验收：search / unique / meta 富化（biz_name/length/is_ownership）。

离线 pytest（Fast + SQLite，无 LLM 无真浏览器）。
"""
from __future__ import annotations

import pytest

from datamason.auth.service import hash_password
from datamason.models import Deployment, User

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

UNIQUE_MSG_SPEC = {
    "biz_notes": "访客留言（姓名唯一）",
    "tables": [
        {
            "name": "messages",
            "biz_name": "留言",
            "columns": [
                {"name": "id", "biz_name": "主键", "type": "INTEGER",
                 "is_pk": True, "autoincrement": True, "nullable": False},
                {"name": "visitor", "biz_name": "访客", "type": "VARCHAR",
                 "length": 64, "nullable": False, "unique": True},
                {"name": "published", "biz_name": "公开", "type": "BOOLEAN"},
                {"name": "joined", "biz_name": "入会", "type": "DATETIME"},
                {"name": "content", "biz_name": "内容", "type": "TEXT"},
            ],
            "indexes": [],
        }
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
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        s.add(User(username="laomao", display_name="老猫",
                   password_hash=hash_password("pw"), is_platform_admin=True))
        s.commit()
    assert client.post("/api/v1/auth/login",
                       json={"username": "laomao", "password": "pw"}).status_code == 200


def _deploy(client, biz_name: str = "测试") -> int:
    preview = client.post("/api/v1/deployments/preview",
                          json={"description": "测试"}).get_json()
    result = client.post("/api/v1/deployments/confirm", json={
        "spec_json": preview["spec_json"],
        "biz_name": biz_name,
        "seed_data": False,
    }).get_json()
    return result["deployment_id"]


def _api(did: int, table: str):
    return f"/api/v1/app/{did}/api/tables/{table}"


# ---------- search ----------

def test_search_across_text_columns(app, client, admin, monkeypatch):
    monkeypatch.setattr(
        "datamason.settings.service.get_default_llm_client",
        lambda _app: FakeLlm(LINKED_SPEC),
    )
    did = _deploy(client)
    base = _api(did, "customers")

    for name, phone in [("老猫", "139"), ("小李", "138"), ("Alice", "137")]:
        assert client.post(f"{base}/rows", json={"name": name, "phone": phone}).status_code == 201

    # 按名称搜索（ilike，大小写不敏感）
    resp = client.get(f"{base}/rows?search=老猫").get_json()
    assert resp["total"] == 1
    assert resp["data"][0]["name"] == "老猫"

    # phone 字段也在搜索范围（文本列 OR）
    resp2 = client.get(f"{base}/rows?search=138").get_json()
    assert resp2["total"] == 1
    assert resp2["data"][0]["name"] == "小李"

    # 大小写不敏感（alice → 匹配 Alice）
    resp3 = client.get(f"{base}/rows?search=alice").get_json()
    assert resp3["total"] == 1
    assert resp3["data"][0]["name"] == "Alice"

    # 无匹配 → 0
    assert client.get(f"{base}/rows?search=zzzz").get_json()["total"] == 0

    # search 不影响计数（创建第二条再查总数）
    assert client.post(f"{base}/rows", json={"name": "老猫2", "phone": "136"}).status_code == 201
    total_resp = client.get(f"{base}/rows").get_json()
    assert total_resp["total"] == 4


def test_search_no_text_columns_ignored(app, client, admin, monkeypatch):
    """orders 表只有整数列，search 参数被忽略，返回全部行。"""
    monkeypatch.setattr(
        "datamason.settings.service.get_default_llm_client",
        lambda _app: FakeLlm(LINKED_SPEC),
    )
    did = _deploy(client)
    cust_base = _api(did, "customers")
    assert client.post(f"{cust_base}/rows", json={"name": "张三"}).status_code == 201

    ord_base = _api(did, "orders")
    for cid in [1]:
        assert client.post(f"{ord_base}/rows",
                           json={"customer_id": cid, "amount": 100}).status_code == 201

    # search 无文本列 → 不加 ilike 条件，返回全部
    resp = client.get(f"{ord_base}/rows?search=abcde").get_json()
    assert resp["total"] == 1


# ---------- meta unique ----------

def test_meta_unique_flag_reflected(app, client, admin, monkeypatch):
    monkeypatch.setattr(
        "datamason.settings.service.get_default_llm_client",
        lambda _app: FakeLlm(UNIQUE_MSG_SPEC),
    )
    did = _deploy(client, biz_name="唯一验证")
    meta = client.get(f"/api/v1/app/{did}/api/meta").get_json()
    cols = {c["name"]: c for c in meta["messages"]["columns"]}
    # visitor 字段声明了 unique=True
    assert cols["visitor"]["unique"] is True
    # id 是主键（pk），不带 unique 标记
    assert cols["id"]["unique"] is False
    # content 未声明 unique
    assert cols["content"]["unique"] is False
    # BOOLEAN 语义类型来自 TableSpec（SQLite 反射为 INTEGER，前端需 BOOLEAN 渲染开关/下拉）
    assert cols["published"]["type"] == "BOOLEAN"


def test_meta_unique_false_when_not_declared(app, client, admin, monkeypatch):
    monkeypatch.setattr(
        "datamason.settings.service.get_default_llm_client",
        lambda _app: FakeLlm(LINKED_SPEC),
    )
    did = _deploy(client)
    meta = client.get(f"/api/v1/app/{did}/api/meta").get_json()
    for tbl in ["customers", "orders"]:
        for c in meta[tbl]["columns"]:
            assert c["unique"] is False


# ---------- 类型语义（BOOLEAN/DATETIME 插入与回读） ----------

def test_create_row_boolean_and_datetime(app, client, admin, monkeypatch):
    monkeypatch.setattr(
        "datamason.settings.service.get_default_llm_client",
        lambda _app: FakeLlm(UNIQUE_MSG_SPEC),
    )
    did = _deploy(client, biz_name="类型语义")
    base = _api(did, "messages")

    resp = client.post(f"{base}/rows", json={
        "visitor": "类型测试",
        "published": 1,
        "joined": "2024-06-15 08:30:00",
        "content": "你好",
    })
    assert resp.status_code == 201, resp.get_data(as_text=True)
    mid = resp.get_json()["id"]

    one = client.get(f"{base}/rows/{mid}").get_json()
    # datetime 字符串被权威地序列化为可回填格式（非 RFC822）
    assert one["joined"] == "2024-06-15 08:30:00"
    assert one["published"] == 1
    assert one["content"] == "你好"


# ---------- meta 富化 ----------

def test_meta_biz_name_and_length_enriched(app, client, admin, monkeypatch):
    monkeypatch.setattr(
        "datamason.settings.service.get_default_llm_client",
        lambda _app: FakeLlm(LINKED_SPEC),
    )
    did = _deploy(client)
    meta = client.get(f"/api/v1/app/{did}/api/meta").get_json()
    cols = {c["name"]: c for c in meta["customers"]["columns"]}

    # biz_name 来自 TableSpec
    assert cols["name"]["biz_name"] == "客户名"
    assert cols["phone"]["biz_name"] == "电话"
    assert cols["id"]["biz_name"] == "主键"

    # length 来自 TableSpec
    assert cols["name"]["length"] == 64
    assert cols["phone"]["length"] == 32
    assert cols["id"]["length"] is None  # INTEGER 无长度


def test_meta_is_ownership_marked(app, client, admin, monkeypatch):
    monkeypatch.setattr(
        "datamason.settings.service.get_default_llm_client",
        lambda _app: FakeLlm(LINKED_SPEC),
    )
    did = _deploy(client)
    meta = client.get(f"/api/v1/app/{did}/api/meta").get_json()
    cols = {c["name"]: c for c in meta["customers"]["columns"]}

    # 归属列
    assert cols["created_by"]["is_ownership"] is True
    assert cols["created_at"]["is_ownership"] is True
    assert cols["updated_by"]["is_ownership"] is True
    assert cols["updated_at"]["is_ownership"] is True

    # 业务列
    assert cols["name"]["is_ownership"] is False
    assert cols["phone"]["is_ownership"] is False
