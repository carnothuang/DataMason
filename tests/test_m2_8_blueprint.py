"""M2-8 数据蓝图—结构变更 diff 提案（生成即提案，确认后 ALTER，消化 B3 遗留）。

- propose：只算不执行——返回人类可读 diff + 方言化 ALTER 语句 + 变更后列蓝图，
  数据库零变化（schema 与行数断言）
- 确定性：propose 出的语句与随后 apply（alter_columns）完全一致
- 确认落地：apply 执行 ALTER 并回写蓝图 columns_json + 结构日志
- 保护：主键/系统归属列/外键列在 propose 阶段即拒，不落库
- 直连红线（DR-05/09）：link 模式 propose/apply 一律拒绝（400），外部库快照零变化
- 蓝图只读：get_blueprint 返回全表 + FK 指向（is_fk/ref_table），供 ER 与多表导航
- DR-14：工程室动作（blueprint/propose/apply）创建人独有，operator 403
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

from datamason.auth.service import hash_password
from datamason.models import Deployment, DeploymentMember, User

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

EXTERNAL_DDL = """
CREATE TABLE customers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name VARCHAR(100) NOT NULL,
  created_by INTEGER,
  created_at DATETIME,
  updated_by INTEGER,
  updated_at DATETIME
);
CREATE TABLE orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  customer_id INTEGER REFERENCES customers(id),
  amount DECIMAL(10, 2),
  note TEXT,
  created_at DATETIME
);
"""


class FakeLlm:
    def __init__(self, payload):
        self.payload = payload

    def chat_json(self, system_prompt, user_prompt):
        class _Usage:
            tokens_in = tokens_out = duration_ms = 1

        return self.payload, _Usage()


@pytest.fixture()
def admin(app, client):
    """建管理员并让共享 client 登录（部署的创建人）。"""
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
        lambda _app: FakeLlm(GUESTBOOK_SPEC),
    )
    yield


@pytest.fixture()
def patch_llm_inv(monkeypatch):
    monkeypatch.setattr(
        "datamason.settings.service.get_default_llm_client",
        lambda _app: FakeLlm(INVENTORY_SPEC),
    )
    yield


@pytest.fixture()
def ext_db(tmp_path) -> str:
    path = str(tmp_path / "external.db")
    con = __import__("sqlite3").connect(path)
    con.executescript(EXTERNAL_DDL)
    con.execute("INSERT INTO customers (name, created_by) VALUES ('老猫', 1)")
    con.execute("INSERT INTO orders (customer_id, amount, note) VALUES (1, 10.5, '首单')")
    con.commit()
    con.close()
    return path


def _session(app):
    return app.extensions["datamason_session_factory"]()


def _deploy_with_spec(client, spec: dict, biz: str) -> int:
    """绕过 LLM preview，直接按确定性 spec 落库（留言板/进销存/直连都能用）。"""
    result = client.post("/api/v1/deployments/confirm", json={
        "spec_json": spec, "biz_name": biz, "seed_data": True,
    })
    assert result.status_code == 201, result.get_data(as_text=True)
    return result.get_json()["deployment_id"]


def _deploy(client, biz="留言板蓝图"):
    return _deploy_with_spec(client, GUESTBOOK_SPEC, biz)


def _biz_engine(app, did):
    with _session(app) as s:
        d = s.get(Deployment, did)
        if d.data_mode == "create":
            db_path = Path(app.config["DATA_ROOT"]) / d.db_name
            return create_engine(f"sqlite:///{db_path.as_posix()}")
        return None


def _propose(client, did, table, ops):
    return client.post(f"/api/v1/deployments/{did}/structure/propose",
                       json={"table": table, "ops": ops})


def _apply(client, did, table, ops):
    return client.post(f"/api/v1/deployments/{did}/tables/{table}/columns",
                       json={"ops": ops})


def _op_add(name, type_="VARCHAR", biz="", length=128):
    return {"action": "add", "name": name, "biz_name": biz,
            "type": type_, "length": length}


def _inspect_cols(engine, table):
    return {c["name"]: c for c in inspect(engine).get_columns(table)}


# ---------------- propose：只算不执行 ----------------

def test_propose_returns_diff_without_applying(app, client, admin, patch_llm):
    did = _deploy(client)
    eng = _biz_engine(app, did)

    r = _propose(client, did, "messages", [_op_add("email", biz="邮箱", length=100)])
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["dirty"] is True
    assert body["diff"] == [{"action": "add", "name": "email", "biz_name": "邮箱",
                             "type": "VARCHAR", "length": 100}]
    assert body["statements"], "应生成 ALTER 语句"
    # 不落库：schema 与行数零变化
    cols = _inspect_cols(eng, "messages")
    assert "email" not in cols
    with eng.connect() as con:
        assert con.execute(text("SELECT COUNT(*) FROM messages")).scalar() == 10
    # 提案里能预览最终蓝图（含新增列）
    names = [c["name"] for c in body["final_columns"]]
    assert "email" in names


def test_propose_unknown_table_404(app, client, admin, patch_llm):
    did = _deploy(client)
    r = _propose(client, did, "ghost", [_op_add("x")])
    assert r.status_code == 404


def test_propose_diff_covers_rename_and_change_type(app, client, admin, patch_llm):
    did = _deploy(client)
    eng = _biz_engine(app, did)

    r = _propose(client, did, "messages", [
        {"action": "rename", "name": "visitor", "new_name": "guest"},
        {"action": "change_type", "name": "content", "type": "VARCHAR", "length": 500},
    ])
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    actions = [d["action"] for d in body["diff"]]
    assert actions == ["rename", "change_type"]
    assert body["diff"][0] == {"action": "rename", "old": "visitor", "name": "guest"}
    assert body["diff"][1] == {"action": "change_type", "name": "content",
                               "type": "VARCHAR", "length": 500}
    assert body["statements"], "rename/change_type 走 SQLite 12 步重建"
    assert "guest" not in _inspect_cols(eng, "messages")  # 仍未执行


def test_propose_statements_match_confirm_apply(app, client, admin, patch_llm):
    """确定性：提案语句 == 确认执行的语句。"""
    did = _deploy(client)
    eng = _biz_engine(app, did)
    ops = [_op_add("email", biz="邮箱")]

    prop = _propose(client, did, "messages", ops).get_json()
    applied = _apply(client, did, "messages", ops)
    assert applied.status_code == 200, applied.get_data(as_text=True)
    assert applied.get_json()["statements"] == prop["statements"]

    # 落库：schema 更新 + 蓝图回写
    assert "email" in _inspect_cols(eng, "messages")
    with _session(app) as s:
        spec = s.query(__import__("datamason.models", fromlist=["TableSpec"]).TableSpec) \
            .filter_by(deployment_id=did, name="messages").one()
        cols = {c["name"]: c for c in json.loads(spec.columns_json)}
        assert cols["email"]["biz_name"] == "邮箱"

    # 结构动作写操作日志（审计闭环）
    with _session(app) as s:
        log = (s.query(__import__("datamason.models", fromlist=["ActivityLog"]).ActivityLog)
               .filter_by(deployment_id=did, action="structure",
                          object_kind="table", object_table="messages")
               .order_by(__import__("datamason.models", fromlist=["ActivityLog"]).ActivityLog.id.desc())
               .first())
        assert log is not None
        assert {"action": "add", "name": "email"} in log.changes.get("ops", [])


def test_propose_rejects_protected_invalid_without_apply(app, client, admin, patch_llm):
    did = _deploy(client)
    eng = _biz_engine(app, did)
    before = set(_inspect_cols(eng, "messages"))

    # 删主键
    r = _propose(client, did, "messages", [{"action": "drop", "name": "id"}])
    assert r.status_code == 400 and "主键" in r.get_json()["error"]
    # 改系统归属列
    r = _propose(client, did, "messages", [{"action": "rename", "name": "created_by",
                                            "new_name": "owner"}])
    assert r.status_code == 400 and "归属" in r.get_json()["error"]
    # 非法类型 + 无操作
    r = _propose(client, did, "messages", [{"action": "change_type",
                                            "name": "content", "type": "FOO"}])
    assert r.status_code == 400
    r = _propose(client, did, "messages", [])
    assert r.status_code == 400

    assert set(_inspect_cols(eng, "messages")) == before  # 全程未落库


# ---------------- 直连红线：link 模式拒绝一切 ALTER ----------------

def test_propose_and_apply_refused_on_link_never_alters_external(app, client, admin, ext_db):
    Session = app.extensions["datamason_session_factory"]
    # 注册外部连接并直连部署
    conn_id = client.post("/api/v1/settings/connections", json={
        "dialect": "sqlite", "name": "外部库", "db_path": ext_db,
    }).get_json()["id"]
    preview = client.post("/api/v1/deployments/link/preview", json={
        "connection_id": conn_id, "tables": ["customers", "orders"],
        "purpose": "客户订单",
    }).get_json()
    res = client.post("/api/v1/deployments/confirm", json={
        "spec_json": preview["spec_json"], "biz_name": "直连客户系统",
        "mode": "link", "connection_id": conn_id,
    })
    assert res.status_code == 201, res.get_data(as_text=True)
    did = res.get_json()["deployment_id"]

    before_ddl, before_counts = _snapshot(ext_db)

    # propose / apply 都被拒（400，绝不对外部库 ALTER）
    r = _propose(client, did, "orders", [_op_add("status", biz="状态")])
    assert r.status_code == 400 and "受保护" in r.get_json()["error"]
    r = _apply(client, did, "orders", [_op_add("status")])
    assert r.status_code == 400 and "受保护" in r.get_json()["error"]

    after_ddl, after_counts = _snapshot(ext_db)
    assert after_ddl == before_ddl
    assert after_counts == before_counts

    # 蓝图仍可只读浏览（ER 数据来源）
    bp = client.get(f"/api/v1/deployments/{did}/blueprint")
    assert bp.status_code == 200
    assert {t["name"] for t in bp.get_json()} == {"customers", "orders"}


def _snapshot(db_path: str):
    import sqlite3

    con = sqlite3.connect(db_path)
    ddl = con.execute(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type IN ('table','index') AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    counts = {}
    for name in {n for n, _s in ddl}:
        try:
            counts[name] = con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        except sqlite3.OperationalError:
            pass
    con.close()
    return ddl, counts


# ---------------- 蓝图数据：多表导航 + ER 指向 ----------------

def test_blueprint_returns_all_tables_and_fk_edges(app, client, admin, patch_llm_inv):
    from datamason.ddl.table_spec import SchemaSpec

    SchemaSpec.model_validate(INVENTORY_SPEC)
    did = _deploy_with_spec(client, INVENTORY_SPEC, "进销存蓝图")

    bp = client.get(f"/api/v1/deployments/{did}/blueprint")
    assert bp.status_code == 200
    rows = bp.get_json()
    names = {t["name"] for t in rows}
    assert names == {"product", "inbound_order"}

    order = next(t for t in rows if t["name"] == "inbound_order")
    pid = next(c for c in order["columns"] if c["name"] == "product_id")
    assert pid["is_fk"] is True and pid["ref_table"] == "product"
    # 每张表都带业务名与富化列（ER 节点/多表导航直接可用）
    for t in rows:
        assert t["biz_name"]
        assert any(c.get("is_pk") for c in t["columns"])


# ---------------- DR-14：工程室动作创建人独有 ----------------

def test_blueprint_propose_apply_creator_only(app, client, admin, patch_llm):
    did = _deploy(client)

    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        u = User(username="xiaoli", display_name="小丽",
                 password_hash=hash_password("pw"), is_platform_admin=False)
        s.add(u)
        s.commit()
        member = DeploymentMember(deployment_id=did, user_id=u.id, role="operator")
        s.add(member)
        s.commit()

    op = app.test_client()
    assert op.post("/api/v1/auth/login",
                   json={"username": "xiaoli", "password": "pw"}).status_code == 200

    assert op.get(f"/api/v1/deployments/{did}/blueprint").status_code == 403
    assert _propose(op, did, "messages", [_op_add("x")]).status_code == 403
    assert _apply(op, did, "messages", [_op_add("x")]).status_code == 403