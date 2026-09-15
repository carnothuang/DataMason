"""M1-4 表编辑器 v0：单表字段增删/改名/改类型（消化 B3）。

离线（SQLite 真迁移）+ MySQL/PG DDL 生成断言；不触网、不调 LLM（注入 FakeLlm）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

from server.auth.service import hash_password
from server.ddl.migration import build_migration
from server.ddl.table_spec import TableSpecModel
from server.models import Deployment, User

# 留言板单表
GUESTBOOK_SPEC = {
    "biz_notes": "留言",
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

# 两表 + 外键（进销存），用于跨表引用保护
INVENTORY_SPEC = {
    "biz_notes": "进销存",
    "tables": [
        {
            "name": "product",
            "biz_name": "商品",
            "columns": [
                {"name": "id", "type": "INTEGER", "is_pk": True,
                 "autoincrement": True, "nullable": False},
                {"name": "name", "biz_name": "名称", "type": "VARCHAR",
                 "length": 128, "nullable": False},
                {"name": "unit_price", "biz_name": "单价", "type": "DECIMAL"},
            ],
            "indexes": [],
        },
        {
            "name": "inbound_order",
            "biz_name": "入库单",
            "columns": [
                {"name": "id", "type": "INTEGER", "is_pk": True,
                 "autoincrement": True, "nullable": False},
                {"name": "product_id", "biz_name": "商品", "type": "BIGINT",
                 "is_fk": True, "ref_table": "product", "ref_column": "id",
                 "nullable": False},
                {"name": "quantity", "biz_name": "数量", "type": "INTEGER"},
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
            tokens_in = tokens_out = duration_ms = 1

        return self.payload, _Usage()


@pytest.fixture()
def admin(app, client):
    """建管理员并让共享 client 登录（M2-1 起业务接口需会话）。"""
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
        "server.settings.service.get_default_llm_client",
        lambda _app: FakeLlm(GUESTBOOK_SPEC),
    )
    yield


@pytest.fixture()
def patch_llm_inv(monkeypatch):
    monkeypatch.setattr(
        "server.settings.service.get_default_llm_client",
        lambda _app: FakeLlm(INVENTORY_SPEC),
    )
    yield


def _session(app):
    return app.extensions["datamason_session_factory"]()


def _deploy(client, biz="留言板编辑", seed=True):
    preview = client.post("/api/v1/deployments/preview",
                          json={"description": "x"}).get_json()
    result = client.post("/api/v1/deployments/confirm", json={
        "spec_json": preview["spec_json"],
        "biz_name": biz, "seed_data": seed,
    }).get_json()
    return result["deployment_id"]


def _biz_engine(app, did):
    with _session(app) as s:
        d = s.get(Deployment, did)
        db_path = Path(app.config["DATA_ROOT"]) / d.db_name
    eng = create_engine(f"sqlite:///{db_path.as_posix()}")
    return eng


# ---------------- 蓝图读取 ----------------

def test_blueprint_enriched(app, client, admin, patch_llm):
    did = _deploy(client)
    bp = client.get(f"/api/v1/deployments/{did}/blueprint").get_json()
    table = next(t for t in bp if t["name"] == "messages")
    col = {c["name"]: c for c in table["columns"]}
    assert col["id"]["is_pk"] is True
    assert col["created_by"]["is_ownership"] is True
    assert col["visitor"]["type"] == "VARCHAR" and col["visitor"]["length"] == 64
    # 归属列与主键均在蓝图中
    assert set(col) >= {"created_by", "created_at", "updated_by", "updated_at"}


# ---------------- 新增列 ----------------

def test_add_column(app, client, admin, patch_llm):
    did = _deploy(client)
    resp = client.post(
        f"/api/v1/deployments/{did}/tables/messages/columns",
        json={"ops": [{"action": "add", "name": "email",
                       "biz_name": "邮箱", "type": "VARCHAR", "length": 128}]},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    names = [c["name"] for c in resp.get_json()["columns"]]
    assert "email" in names

    # 物理表真实存在该列，且旧数据未丢
    biz = _biz_engine(app, did)
    cols = {c["name"] for c in inspect(biz).get_columns("messages")}
    assert "email" in cols
    with biz.connect() as conn_db:
        assert conn_db.execute(text("SELECT COUNT(*) FROM messages")).scalar() == 10
    biz.dispose()

    # 浏览接口仍正常（反射重建自动生效）
    rows = client.get(
        f"/api/v1/deployments/{did}/tables/messages/rows?page=1&page_size=3"
    ).get_json()
    assert rows["total"] == 10 and "email" in rows["columns"]


def test_add_duplicate_column_rejected(app, client, admin, patch_llm):
    did = _deploy(client)
    resp = client.post(
        f"/api/v1/deployments/{did}/tables/messages/columns",
        json={"ops": [{"action": "add", "name": "visitor", "type": "TEXT"}]},
    )
    assert resp.status_code == 400 and "已存在" in resp.get_json()["error"]


def test_add_bad_type_rejected(app, client, admin, patch_llm):
    did = _deploy(client)
    resp = client.post(
        f"/api/v1/deployments/{did}/tables/messages/columns",
        json={"ops": [{"action": "add", "name": "weird", "type": "BLOB_X"}]},
    )
    assert resp.status_code == 400


# ---------------- 删除列 ----------------

def test_drop_column_preserves_other_data(app, client, admin, patch_llm):
    did = _deploy(client)
    resp = client.post(
        f"/api/v1/deployments/{did}/tables/messages/columns",
        json={"ops": [{"action": "drop", "name": "content"}]},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert "content" not in [c["name"] for c in resp.get_json()["columns"]]

    biz = _biz_engine(app, did)
    cols = {c["name"] for c in inspect(biz).get_columns("messages")}
    assert "content" not in cols and "visitor" in cols
    with biz.connect() as conn_db:
        assert conn_db.execute(text("SELECT COUNT(*) FROM messages")).scalar() == 10
        # visitor 数据在重建后保留
        n = conn_db.execute(
            text("SELECT COUNT(*) FROM messages WHERE visitor IS NOT NULL")
        ).scalar()
        assert n == 10
    biz.dispose()


def test_drop_pk_rejected(app, client, admin, patch_llm):
    did = _deploy(client)
    resp = client.post(
        f"/api/v1/deployments/{did}/tables/messages/columns",
        json={"ops": [{"action": "drop", "name": "id"}]},
    )
    assert resp.status_code == 400 and "主键" in resp.get_json()["error"]


def test_drop_ownership_rejected(app, client, admin, patch_llm):
    did = _deploy(client)
    resp = client.post(
        f"/api/v1/deployments/{did}/tables/messages/columns",
        json={"ops": [{"action": "drop", "name": "created_by"}]},
    )
    assert resp.status_code == 400 and "归属列" in resp.get_json()["error"]


# ---------------- 改名 ----------------

def test_rename_column_keeps_data(app, client, admin, patch_llm):
    did = _deploy(client)
    resp = client.post(
        f"/api/v1/deployments/{did}/tables/messages/columns",
        json={"ops": [{"action": "rename", "name": "content", "new_name": "body"}]},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    biz = _biz_engine(app, did)
    cols = {c["name"] for c in inspect(biz).get_columns("messages")}
    assert "body" in cols and "content" not in cols
    with biz.connect() as conn_db:
        assert conn_db.execute(text("SELECT COUNT(*) FROM messages WHERE body IS NOT NULL")).scalar() == 10
    biz.dispose()


def test_rename_to_illegal_identifier_rejected(app, client, admin, patch_llm):
    did = _deploy(client)
    resp = client.post(
        f"/api/v1/deployments/{did}/tables/messages/columns",
        json={"ops": [{"action": "rename", "name": "content", "new_name": "1bad name"}]},
    )
    assert resp.status_code == 400


# ---------------- 改类型 ----------------

def test_change_type(app, client, admin, patch_llm):
    did = _deploy(client)
    resp = client.post(
        f"/api/v1/deployments/{did}/tables/messages/columns",
        json={"ops": [{"action": "change_type", "name": "visitor",
                       "type": "VARCHAR", "length": 256}]},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    col = next(c for c in resp.get_json()["columns"] if c["name"] == "visitor")
    assert col["type"] == "VARCHAR" and col["length"] == 256


# ---------------- 外键 / 跨表引用保护 ----------------

def test_fk_column_protected(app, client, admin, patch_llm_inv):
    did = _deploy(client, biz="进销存")
    resp = client.post(
        f"/api/v1/deployments/{did}/tables/inbound_order/columns",
        json={"ops": [{"action": "rename", "name": "product_id",
                       "new_name": "pid"}]},
    )
    assert resp.status_code == 400 and "外键" in resp.get_json()["error"]


def test_referenced_pk_protected(app, client, admin, patch_llm_inv):
    did = _deploy(client, biz="进销存")
    # product.id 被 inbound_order.product_id 引用：改类型应被拒
    resp = client.post(
        f"/api/v1/deployments/{did}/tables/product/columns",
        json={"ops": [{"action": "change_type", "name": "id", "type": "BIGINT"}]},
    )
    assert resp.status_code == 400  # 主键受保护（同时也被引用）


def test_inventory_add_normal_column(app, client, admin, patch_llm_inv):
    did = _deploy(client, biz="进销存")
    resp = client.post(
        f"/api/v1/deployments/{did}/tables/product/columns",
        json={"ops": [{"action": "add", "name": "remark", "type": "TEXT"}]},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    biz = _biz_engine(app, did)
    cols = {c["name"] for c in inspect(biz).get_columns("product")}
    assert "remark" in cols
    biz.dispose()


# ---------------- 入参 / 路由 ----------------

def test_empty_ops_rejected(app, client, admin, patch_llm):
    did = _deploy(client)
    assert client.post(
        f"/api/v1/deployments/{did}/tables/messages/columns",
        json={"ops": []},
    ).status_code == 400


def test_unknown_table_404(app, client, admin, patch_llm):
    did = _deploy(client)
    resp = client.post(
        f"/api/v1/deployments/{did}/tables/ghost/columns",
        json={"ops": [{"action": "add", "name": "x", "type": "TEXT"}]},
    )
    assert resp.status_code == 404


def test_unknown_deployment_404(app, client, admin):
    resp = client.post(
        "/api/v1/deployments/999/tables/messages/columns",
        json={"ops": [{"action": "add", "name": "x", "type": "TEXT"}]},
    )
    assert resp.status_code == 404


# ---------------- MySQL / PG DDL 生成（不连真库） ----------------

def _guest_model():
    return TableSpecModel.model_validate(GUESTBOOK_SPEC["tables"][0])


def test_mysql_add_drop_rename_change_sql():
    from server.ddl.ddl_builder import inject_ownership_columns
    t = inject_ownership_columns(_guest_model())
    ops = [
        {"action": "add", "name": "email", "type": "VARCHAR", "length": 128},
        {"action": "drop", "name": "content"},
        {"action": "rename", "name": "visitor", "new_name": "nick"},
        {"action": "change_type", "name": "nick", "type": "VARCHAR", "length": 256},
    ]
    _final, stmts = build_migration(t, ops, [t], "mysql")
    sql = stmts[0]
    assert sql.startswith("ALTER TABLE `messages`")
    assert "ADD COLUMN `email` VARCHAR(128)" in sql
    assert "DROP COLUMN `content`" in sql
    # 改名+同批改类型合并进 CHANGE（最终长度 256），冗余 MODIFY 被抑制
    assert "CHANGE COLUMN `visitor` `nick` VARCHAR(256) NOT NULL" in sql
    assert "MODIFY COLUMN `nick`" not in sql
    # 归属列仍在最终蓝图
    assert {c.name for c in _final} >= {"created_by", "updated_at"}


def test_pg_add_drop_rename_change_sql():
    from server.ddl.ddl_builder import inject_ownership_columns
    t = inject_ownership_columns(_guest_model())
    ops = [
        {"action": "drop", "name": "content"},
        {"action": "rename", "name": "visitor", "new_name": "nick"},
    ]
    _final, stmts = build_migration(t, ops, [t], "postgresql")
    sql = stmts[0]
    assert sql.startswith('ALTER TABLE "messages"')
    assert 'DROP COLUMN "content"' in sql
    assert 'RENAME COLUMN "visitor" TO "nick"' in sql


def test_sqlite_add_uses_lightweight_alter():
    from server.ddl.ddl_builder import inject_ownership_columns
    t = inject_ownership_columns(_guest_model())
    _final, stmts = build_migration(
        t, [{"action": "add", "name": "email", "type": "TEXT"}], [t], "sqlite"
    )
    assert len(stmts) == 1
    assert stmts[0] == "ALTER TABLE messages ADD COLUMN email TEXT NULL"
