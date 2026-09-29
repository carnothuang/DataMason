"""M6-1/M6-2 验收：混合建库两段式后端路由（骨架提案 / 逐表字段）。

仅创建模式开放（DR-39）；骨架/字段不落库，随请求回传（DR-37）；
LLM 失败中文回喂重试，仍失败 502 且记 error 埋点。
"""
from __future__ import annotations

import pytest

from datamason.auth.service import hash_password
from datamason.models import AiGeneration, User

SKELETON = {
    "biz_notes": "客户、订单与明细",
    "tables": [
        {"name": "customers", "biz_name": "客户", "relations": []},
        {"name": "orders", "biz_name": "订单",
         "relations": [{"to_table": "customers"}]},
        {"name": "order_items", "biz_name": "明细",
         "relations": [{"to_table": "orders"}]},
    ],
}

ORDER_COLUMNS = {
    "columns": [
        {"name": "amount", "biz_name": "金额", "type": "DECIMAL", "nullable": False},
    ],
}

BAD = {"tables": [{"name": "Bad Name", "relations": []}]}


class _Usage:
    tokens_in = 3
    tokens_out = 5
    duration_ms = 7


class FakeLlm:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def chat_json(self, system_prompt, user_prompt):
        self.calls += 1
        idx = min(self.calls - 1, len(self.replies) - 1)
        return self.replies[idx], _Usage()


@pytest.fixture()
def admin(app, client):
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        u = User(username="laomao", display_name="老猫",
                 password_hash=hash_password("pw"), is_platform_admin=True)
        s.add(u)
        s.commit()
    resp = client.post("/api/v1/auth/login",
                       json={"username": "laomao", "password": "pw"})
    assert resp.status_code == 200


@pytest.fixture()
def patch_llm(monkeypatch):
    holder = {"fn": None}

    def _client_for(_app):
        return holder["fn"]()

    monkeypatch.setattr("datamason.settings.service.get_default_llm_client", _client_for)
    return lambda fn: holder.__setitem__("fn", fn)


def _records(app):
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        return s.query(AiGeneration).order_by(AiGeneration.id).all()


# ---------------- M6-1 骨架提案 ----------------

def test_propose_skeleton_ok(app, client, admin, patch_llm):
    patch_llm(lambda: FakeLlm([SKELETON]))
    resp = client.post("/api/v1/deployments/skeleton/propose",
                       json={"description": "客户下单", "mode": "create"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["attempts"] == 1
    assert data["biz_notes"] == "客户、订单与明细"
    assert data["skeleton_json"]["tables"][1]["name"] == "orders"
    # 外键列占位：orders 引用 customers → customers_id；每表含 id 主键占位
    assert [c["name"] for c in data["columns"]["customers"]] == ["id"]
    assert [c["name"] for c in data["columns"]["orders"]] == ["id", "customers_id"]
    assert data["columns"]["orders"][1]["is_fk"] is True
    assert data["columns"]["orders"][1]["ref_table"] == "customers"
    # 埋点：一条 deploy/ok
    rows = _records(app)
    assert len(rows) == 1 and rows[0].kind == "deploy" and rows[0].status == "ok"


def test_propose_skeleton_rejects_link_mode(app, client, admin, patch_llm):
    patch_llm(lambda: FakeLlm([SKELETON]))
    resp = client.post("/api/v1/deployments/skeleton/propose",
                       json={"description": "x", "mode": "link"})
    assert resp.status_code == 400
    assert "创建模式" in resp.get_json()["error"]
    assert _records(app) == []


def test_propose_skeleton_requires_description(app, client, admin, patch_llm):
    patch_llm(lambda: FakeLlm([SKELETON]))
    assert client.post("/api/v1/deployments/skeleton/propose",
                       json={}).status_code == 400
    assert client.post("/api/v1/deployments/skeleton/propose",
                       json={"description": "x", "mode": "weird"}).status_code == 400


def test_propose_skeleton_without_llm_config(app, client, admin, monkeypatch):
    monkeypatch.setattr("datamason.settings.service.get_default_llm_client",
                        lambda _app: None)
    resp = client.post("/api/v1/deployments/skeleton/propose",
                       json={"description": "客户下单"})
    assert resp.status_code == 400
    assert "LLM" in resp.get_json()["error"]


def test_propose_skeleton_retry_then_success(app, client, admin, patch_llm):
    patch_llm(lambda: FakeLlm([BAD, SKELETON]))
    resp = client.post("/api/v1/deployments/skeleton/propose",
                       json={"description": "客户下单"})
    assert resp.status_code == 200
    assert resp.get_json()["attempts"] == 2
    rows = _records(app)
    assert len(rows) == 1 and rows[0].status == "ok"


def test_propose_skeleton_failure_502_and_trace(app, client, admin, patch_llm):
    patch_llm(lambda: FakeLlm([BAD]))
    resp = client.post("/api/v1/deployments/skeleton/propose",
                       json={"description": "客户下单"})
    assert resp.status_code == 502
    rows = _records(app)
    assert len(rows) == 1 and rows[0].status == "error"
    assert rows[0].kind == "deploy"


# ---------------- M6-2 逐表字段 ----------------

def _skeleton_of(app, client, admin, patch_llm):
    patch_llm(lambda: FakeLlm([SKELETON]))
    return client.post("/api/v1/deployments/skeleton/propose",
                       json={"description": "客户下单"}).get_json()["skeleton_json"]


def test_generate_columns_overwrite(app, client, admin, patch_llm):
    sk = _skeleton_of(app, client, admin, patch_llm)
    patch_llm(lambda: FakeLlm([ORDER_COLUMNS]))
    resp = client.post("/api/v1/deployments/skeleton/tables/orders/columns",
                       json={"skeleton": sk, "nl": "订单要金额", "mode": "overwrite"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    names = [c["name"] for c in data["columns"]]
    assert names[:2] == ["id", "customers_id"]
    assert "amount" in names
    # 归属列注入
    assert {"created_by", "created_at", "updated_by", "updated_at"} <= set(names)
    # orders 已引导，余下 customers/order_items 未引导
    assert set(data["unguided_tables"]) == {"customers", "order_items"}
    assert data["attempts"] == 1


def test_generate_columns_append(app, client, admin, patch_llm):
    sk = _skeleton_of(app, client, admin, patch_llm)
    patch_llm(lambda: FakeLlm([ORDER_COLUMNS]))
    overwrite = client.post("/api/v1/deployments/skeleton/tables/orders/columns",
                            json={"skeleton": sk, "nl": "金额", "mode": "overwrite"})
    existing = overwrite.get_json()["columns"]

    patch_llm(lambda: FakeLlm([
        {"columns": [{"name": "remark", "biz_name": "备注", "type": "TEXT"}]}]))
    resp = client.post("/api/v1/deployments/skeleton/tables/orders/columns",
                       json={"skeleton": sk, "nl": "再加备注", "mode": "append",
                             "existing_columns": existing,
                             "guided_tables": ["orders"]})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    names = [c["name"] for c in resp.get_json()["columns"]]
    assert "amount" in names and "remark" in names
    assert resp.get_json()["unguided_tables"] == ["customers", "order_items"]


def test_generate_columns_validation(app, client, admin, patch_llm):
    sk = _skeleton_of(app, client, admin, patch_llm)
    patch_llm(lambda: FakeLlm([ORDER_COLUMNS]))
    base = {"skeleton": sk, "nl": "x"}
    # 缺骨架
    assert client.post("/api/v1/deployments/skeleton/tables/orders/columns",
                       json={"nl": "x"}).status_code == 400
    # 表不在骨架
    assert client.post("/api/v1/deployments/skeleton/tables/nope/columns",
                       json=base).status_code == 400
    # 未知模式
    assert client.post("/api/v1/deployments/skeleton/tables/orders/columns",
                       json={**base, "mode": "nuke"}).status_code == 400
    # 空 nl
    assert client.post("/api/v1/deployments/skeleton/tables/orders/columns",
                       json={"skeleton": sk}).status_code == 400
    # append 缺 existing_columns（LLM 调用前即拒）
    assert client.post("/api/v1/deployments/skeleton/tables/orders/columns",
                       json={**base, "mode": "append"}).status_code == 502


def test_generate_columns_failure_traces(app, client, admin, patch_llm):
    sk = _skeleton_of(app, client, admin, patch_llm)
    patch_llm(lambda: FakeLlm([
        {"columns": [{"name": "amount", "type": "MONEY"}]}]))  # 非法类型
    resp = client.post("/api/v1/deployments/skeleton/tables/orders/columns",
                       json={"skeleton": sk, "nl": "金额"})
    assert resp.status_code == 502
    rows = _records(app)
    # 第一次骨架 ok + 本次字段 error
    assert [r.status for r in rows] == ["ok", "error"]
