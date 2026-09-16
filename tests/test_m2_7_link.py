"""M2-7 验收：直连模式首版（DR-09/DR-18 + 两段式选表 + 缺归属列降级明示，绝不 ALTER 外部库）。

- 连接清单：登录可见（非平台管理员也可直连已有连接；/settings 仍 403 隔离）
- 全库清单 inventory：单主键可用性 / created_by / FK 引用链
- 两段式选表 select_recommend：关键词命中 + 引用链邻居，有界确定性；纯中文回退连接度
- 反向生成 preview：真实列/类型白名单/FK 指主键/无主键剔除/归属列原样保留（不注入）
- confirm link：复用连接、不建新库、不执行 DDL、不种种子、状态 running、入口可用
- 绝不 ALTER：直连部署前后外部库 schema 与行数零变化
- 缺归属列：operator「我的记录」在缺 created_by 的表上自然降级（不过滤全量见），
  有 created_by 的表仍强制按人过滤；meta 暴露 is_ownership 供前端明示
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from server.auth.service import hash_password
from server.models import Connection, Deployment, TableSpec, User

EXTERNAL_DDL = """
CREATE TABLE customers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name VARCHAR(100) NOT NULL,
  phone VARCHAR(20),
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
CREATE TABLE feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  content TEXT,
  contact VARCHAR(50)
);
CREATE TABLE raw_log (content TEXT);
"""


@pytest.fixture()
def ext_db(tmp_path) -> str:
    path = str(tmp_path / "external.db")
    con = sqlite3.connect(path)
    con.executescript(EXTERNAL_DDL)
    con.execute("INSERT INTO customers (name, phone, created_by) VALUES ('老猫','13800000000',1)")
    con.execute("INSERT INTO customers (name, phone, created_by) VALUES ('王姐','13800000001',2)")
    con.execute("INSERT INTO orders (customer_id, amount, note) VALUES (1, 10.5, '首单')")
    con.commit()
    con.close()
    return path


@pytest.fixture()
def env(app):
    """平台管理员登录，可创建连接；返回共享 client 与 id。"""
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        u = User(username="laomao", display_name="老猫",
                 password_hash=hash_password("pw"), is_platform_admin=True)
        s.add(u)
        s.commit()
        admin_id = u.id
    c = app.test_client()
    assert c.post("/api/v1/auth/login",
                  json={"username": "laomao", "password": "pw"}).status_code == 200
    return {"client": c, "id": admin_id}


def _mk_user(app, username: str, admin: bool = False) -> int:
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        u = User(username=username, display_name=username,
                 password_hash=hash_password("pw"), is_platform_admin=admin)
        s.add(u)
        s.commit()
        return u.id


def _login(app, username: str, password: str = "pw"):
    c = app.test_client()
    assert c.post("/api/v1/auth/login",
                  json={"username": username, "password": password}).status_code == 200
    return c


def _make_connection(env, app, db_path: str) -> int:
    r = env["client"].post("/api/v1/settings/connections", json={
        "dialect": "sqlite", "name": "外部库", "db_path": db_path,
    })
    assert r.status_code == 201, r.get_data(as_text=True)
    return r.get_json()["id"]


def _snapshot(db_path: str) -> tuple[list, dict]:
    """外部库快照：DDL 序列表 + 业务表行数，用于验证「绝不 ALTER」零变化。"""
    con = sqlite3.connect(db_path)
    ddl = con.execute(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type IN ('table','index') AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    counts = {}
    for name, _sql in ddl:
        if name.startswith("sqlite_"):
            continue
        try:
            counts[name] = con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        except sqlite3.OperationalError:
            pass
    con.close()
    return ddl, counts


# ---------------- 连接清单：登录可见 / /settings 仍管理员隔离 ----------------

def test_connection_list_visible_to_non_admin_but_settings_guarded(app, env, ext_db):
    conn_id = _make_connection(env, app, ext_db)
    _mk_user(app, "deployer")
    c = _login(app, "deployer")
    r = c.get("/api/v1/deployments/link/connections")
    assert r.status_code == 200
    assert any(cn["id"] == conn_id for cn in r.get_json())
    # /settings 连接管理仍是平台管理员专属
    assert c.get("/api/v1/settings/connections").status_code == 403


# ---------------- 全库清单 ----------------

def test_inventory_lists_tables_and_capabilities(app, env, ext_db):
    conn_id = _make_connection(env, app, ext_db)
    rows = env["client"].post("/api/v1/deployments/link/inventory",
                              json={"connection_id": conn_id}).get_json()
    by_name = {t["name"]: t for t in rows}
    assert set(by_name) == {"customers", "orders", "feedback", "raw_log"}
    assert by_name["customers"]["has_created_by"] is True
    assert by_name["orders"]["single_pk"] is True
    assert by_name["raw_log"]["single_pk"] is False
    assert by_name["raw_log"]["pk"] is None
    assert by_name["orders"]["fk_targets"] == ["customers"]
    assert by_name["orders"]["has_created_by"] is False


def test_inventory_unknown_connection_404(app, env):
    r = env["client"].post("/api/v1/deployments/link/inventory",
                           json={"connection_id": 999})
    assert r.status_code == 404


# ---------------- 两段式选表（能力契约 _select_relevant_tables） ----------------

def test_select_recommend_keyword_and_fk_closure(app, env, ext_db):
    conn_id = _make_connection(env, app, ext_db)
    body = env["client"].post("/api/v1/deployments/link/select", json={
        "connection_id": conn_id, "purpose": "orders 与 feedback 概况",
    }).get_json()
    # 关键词命中 orders/feedback；orders 引用链邻居 customers 一并带入；raw_log 无关剔除
    assert set(body["selection"]) == {"orders", "feedback", "customers"}
    assert "raw_log" not in body["selection"]
    assert body["no_pk"] == ["raw_log"]
    assert body["triggered"] is False
    assert body["total"] == 4


def test_select_recommend_chinese_falls_back_to_connectivity(app, env, ext_db):
    conn_id = _make_connection(env, app, ext_db)
    body = env["client"].post("/api/v1/deployments/link/select", json={
        "connection_id": conn_id, "purpose": "客户",
    }).get_json()
    # 纯中文无英文词元 → 回退连接度优先（被引用最多的 customers 打头，其余按名序）
    assert len(body["selection"]) == 4
    assert body["selection"][0] == "customers"


def test_select_relevant_tables_contract_pure(app):
    """契约纯函数：有界、确定性、邻居收敛、零命中全剔除。"""
    from server.dataaccess.select_tables import (
        SELECT_MAX_TABLES,
        TableInventory,
        select_relevant_tables,
    )

    inventory = [TableInventory(name=f"t{i:02d}", columns=("id",), fk_targets=())
                 for i in range(1, 21)]
    inventory += [
        TableInventory("customers", ("id", "name"), ()),
        TableInventory("orders", ("id", "customer_id", "amount"), ("customers",)),
        TableInventory("line_items", ("id", "order_id", "sku"), ("orders",)),
    ]
    r1 = select_relevant_tables(inventory, "orders 当日盘点")
    # 命中 orders；引用链邻居 customers / line_items 一并进入；其余全零命中剔除
    assert set(r1) == {"orders", "customers", "line_items"}
    # 确定性：同输入同输出，且排序稳定（orders 分高在前，邻居按名序）
    assert r1 == select_relevant_tables(inventory, "orders 当日盘点")
    assert r1 == ["orders", "customers", "line_items"]
    assert len(r1) <= SELECT_MAX_TABLES


def test_select_relevant_tables_fallback_bounded(app):
    from server.dataaccess.select_tables import (
        SELECT_MAX_TABLES,
        TableInventory,
        select_relevant_tables,
    )

    inventory = [TableInventory(name=f"v{i:02d}", columns=("id",), fk_targets=())
                 for i in range(1, 25)]
    r = select_relevant_tables(inventory, "完全无关的短语")
    assert len(r) == min(SELECT_MAX_TABLES, len(inventory)) == 12
    assert r == sorted([t.name for t in inventory])[:12]  # 全部零命中按名序截断


# ---------------- 反向生成 / 提案 ----------------

def test_link_preview_reverse_spec(app, env, ext_db):
    conn_id = _make_connection(env, app, ext_db)
    r = env["client"].post("/api/v1/deployments/link/preview", json={
        "connection_id": conn_id, "tables": ["orders", "customers"],
        "purpose": "客户订单",
    })
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["data_mode"] == "link"
    names = {t["name"] for t in body["tables"]}
    assert names == {"orders", "customers"}
    orders = next(t for t in body["tables"] if t["name"] == "orders")
    cid = next(c for c in orders["columns"] if c["name"] == "customer_id")
    assert cid["is_fk"] is True and cid["ref_table"] == "customers"
    # 直连只派生成员 CRUD 页（不开公开写表单）
    assert [p["kind"] for p in body["pages"]] == ["crud_list", "crud_list"]
    assert all(p["access"] == "member" for p in body["pages"])
    # 缺归属列明示：orders 无 created_by；customers 有
    assert body["link"]["ownership_missing"] == ["orders"]
    assert body["link"]["no_pk_tables"] == []
    # spec 可通过 pydantic 强校验（外键必须指向选定集内主键）
    assert body["spec_json"]["tables"]


def test_link_preview_excludes_pkless_and_missing(app, env, ext_db):
    conn_id = _make_connection(env, app, ext_db)
    r = env["client"].post("/api/v1/deployments/link/preview", json={
        "connection_id": conn_id, "tables": ["customers", "raw_log", "ghost"],
    })
    body = r.get_json()
    names = {t["name"] for t in body["tables"]}
    assert names == {"customers"}          # raw_log 无主键被剔除、ghost 不存在被剔除
    assert set(body["link"]["no_pk_tables"]) == {"raw_log", "ghost"}


def test_link_preview_rejects_all_unusable(app, env, ext_db):
    conn_id = _make_connection(env, app, ext_db)
    r = env["client"].post("/api/v1/deployments/link/preview", json={
        "connection_id": conn_id, "tables": ["raw_log"],
    })
    assert r.status_code == 400
    assert "单主键" in r.get_json()["error"]


def test_link_preview_enhance_labels(app, env, ext_db, monkeypatch):
    class _Usage:
        tokens_in = 1
        tokens_out = 1
        duration_ms = 1

    class FakeLlm:
        def __init__(self, payload):
            self.payload = payload

        def chat_json(self, system_prompt, user_prompt):
            return self.payload, _Usage()

    conn_id = _make_connection(env, app, ext_db)
    fake = FakeLlm({
        "biz_notes": "外部客户订单系统",
        "tables": [{"name": "customers", "biz_name": "客户表"},
                   {"name": "orders", "biz_name": "订单表"}],
    })
    monkeypatch.setattr("server.settings.service.get_default_llm_client",
                        lambda _app: fake)
    body = env["client"].post("/api/v1/deployments/link/preview", json={
        "connection_id": conn_id, "tables": ["orders", "customers"],
        "purpose": "客户订单", "enhance": True,
    }).get_json()
    biz = {t["name"]: t["biz_name"] for t in body["tables"]}
    assert biz == {"customers": "客户表", "orders": "订单表"}
    assert body["link"]["enrich_failed"] == ""


def test_link_preview_enhance_without_model_falls_back(app, env, ext_db):
    conn_id = _make_connection(env, app, ext_db)
    body = env["client"].post("/api/v1/deployments/link/preview", json={
        "connection_id": conn_id, "tables": ["orders", "customers"],
        "purpose": "客户订单", "enhance": True,
    }).get_json()
    assert all(t["biz_name"] == t["name"] for t in body["tables"])  # 确定性默认
    assert body["link"]["enrich_failed"]


# ---------------- confirm：直连落库，绝不 ALTER 外部库 ----------------

def _link_deploy(client, conn_id: int, tables: list[str], seed_data: bool = False) -> int:
    preview = client.post("/api/v1/deployments/link/preview", json={
        "connection_id": conn_id, "tables": tables, "purpose": "客户订单",
    }).get_json()
    result = client.post("/api/v1/deployments/confirm", json={
        "spec_json": preview["spec_json"],
        "biz_name": "直连客户系统",
        "mode": "link",
        "connection_id": conn_id,
        "seed_data": seed_data,
    })
    assert result.status_code == 201, result.get_data(as_text=True)
    return result.get_json()["deployment_id"]


def test_link_confirm_reuses_connection_without_alter(app, env, ext_db):
    conn_id = _make_connection(env, app, ext_db)
    before_ddl, before_counts = _snapshot(ext_db)
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        conn_count_before = s.query(Connection).count()

    did = _link_deploy(env["client"], conn_id, ["customers", "orders"], seed_data=True)

    # 连接数不变（复用），新库文件零新增
    with Session() as s:
        assert s.query(Connection).count() == conn_count_before
        d = s.get(Deployment, did)
        assert d.data_mode == "link"
        assert d.connection_id == conn_id
        assert d.db_name == Path(ext_db).name   # 存实际库名，非连接显示名
        assert d.seed_data is False          # 直连不种种子
        assert d.status == "running"
    assert not list((Path(app.config["DATA_ROOT"]) / "deployments").glob("*.db"))

    # 绝不 ALTER：DDL 与行数零变化
    after_ddl, after_counts = _snapshot(ext_db)
    assert after_ddl == before_ddl
    assert after_counts == before_counts

    # 元数据按真实列登记（customers 7 列含 created_by，不注入额外列）
    with Session() as s:
        specs = {t.name: t for t in s.query(TableSpec).filter_by(deployment_id=did).all()}
        assert set(specs) == {"customers", "orders"}
        assert len(specs["customers"].columns) == 7
        assert len(specs["orders"].columns) == 5
    info = env["client"].get(f"/api/v1/app/{did}").get_json()
    assert info["status"] == "running"
    assert info["data_mode"] == "link"
    assert all(p["kind"] == "crud_list" for p in info["pages"])


def test_link_confirm_requires_connection(app, env, ext_db):
    _make_connection(env, app, ext_db)
    preview = env["client"].post("/api/v1/deployments/link/preview", json={
        "connection_id": 1, "tables": ["customers"], "purpose": "客户",
    }).get_json()
    r = env["client"].post("/api/v1/deployments/confirm", json={
        "spec_json": preview["spec_json"], "biz_name": "直连客户系统",
        "mode": "link",
    })
    assert r.status_code == 400
    assert "已有连接" in r.get_json()["error"]


# ---------------- 缺归属列降级明示：operator「我的记录」自然降级 ----------------

def test_operator_mine_degrades_when_created_by_missing(app, env, ext_db):
    conn_id = _make_connection(env, app, ext_db)
    _mk_user(app, "deployer")
    deployer = _login(app, "deployer")
    did = _link_deploy(deployer, conn_id, ["customers", "orders"])
    _mk_user(app, "xiaoli")
    op = _login(app, "xiaoli")

    # 创建人添加操作员并授权两张表菜单
    menus = deployer.get(f"/api/v1/deployments/{did}/members").get_json()["menus"]
    menu_ids = [m["id"] for m in menus]
    assert deployer.post(f"/api/v1/deployments/{did}/members", json={
        "username": "xiaoli", "role": "operator", "menu_ids": menu_ids,
    }).status_code == 201

    meta = op.get(f"/api/v1/app/{did}/api/meta").get_json()
    customers_cols = {c["name"]: c for c in meta["customers"]["columns"]}
    assert customers_cols["created_by"]["is_ownership"] is True
    # orders 无 created_by（created_at 等其余归属列原样保留），故「我的记录」对 orders 不可用
    assert all(c["name"] != "created_by" for c in meta["orders"]["columns"])
    assert any(c["name"] == "created_at" and c["is_ownership"] for c in meta["orders"]["columns"])

    # orders 缺 created_by →「我的记录」过滤自然降级：操作员可读全部行（不过滤）
    rows = op.get(f"/api/v1/app/{did}/api/tables/orders/rows").get_json()
    assert rows["total"] == 1
    # customers 有 created_by → 仍强制按人过滤（外部数据归属值 1/2，操作员两人都读不到）
    rows = op.get(f"/api/v1/app/{did}/api/tables/customers/rows").get_json()
    assert rows["total"] == 0