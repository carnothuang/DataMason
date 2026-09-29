"""M8-1 验收：专业建库 SQL 解析接口 POST /deployments/manual/sql/parse。

任务 M8-1 / DR-41/42/45：把人写的 CREATE TABLE 解析为结构提案——
- 返回 spec 片段；非法 SQL 返回定位到表/列的 errors 清单；
- link 模式拒绝（直连只读红线）；
- 不执行、不落库、不调 LLM、不产生 ai_generations；
- 指定目标连接时以连接方言为准并校验一致性。
"""
from __future__ import annotations

import pytest

from datamason.auth.service import hash_password
from datamason.models import AiGeneration, Connection, User

SQLITE_SQL = """
CREATE TABLE customer (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name VARCHAR(100) NOT NULL,
    phone VARCHAR(20) UNIQUE
);
CREATE TABLE "order" (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customer(id),
    amount DECIMAL(10, 2) NOT NULL
);
"""

PARSE_URL = "/api/v1/deployments/manual/sql/parse"


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


def _records(app):
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        return s.query(AiGeneration).all()


def _make_connection(app, name: str, dialect: str) -> int:
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        c = Connection(name=name, dialect=dialect,
                       conn_uri="x", status="unknown")
        s.add(c)
        s.commit()
        return c.id


def test_parse_ok(app, client, admin):
    resp = client.post(PARSE_URL, json={"sql": SQLITE_SQL, "dialect": "sqlite"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["dialect"] == "sqlite"
    assert data["table_count"] == 2
    tables = data["spec_json"]["tables"]
    assert [t["name"] for t in tables] == ["customer", "order"]
    order_cols = {c["name"]: c for c in tables[1]["columns"]}
    fk = order_cols["customer_id"]
    assert fk["is_fk"] and fk["ref_table"] == "customer" and fk["ref_column"] == "id"
    # 不执行、不落库：data 目录无业务库；不写 ai_generations
    assert _records(app) == []


def test_parse_bad_sql_returns_locations(app, client, admin):
    bad = "CREATE TABLE t (id INTEGER PRIMARY KEY, payload JSON NOT NULL);"
    resp = client.post(PARSE_URL, json={"sql": bad, "dialect": "sqlite"})
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["errors"]
    first = data["errors"][0]
    assert first["table"] == "t" and first["column"] == "payload"
    assert "不受支持" in first["message"]
    assert _records(app) == []


def test_parse_rejects_link_mode(app, client, admin):
    resp = client.post(PARSE_URL,
                       json={"sql": SQLITE_SQL, "dialect": "sqlite", "mode": "link"})
    assert resp.status_code == 400
    assert "创建模式" in resp.get_json()["error"]
    assert _records(app) == []


def test_parse_rejects_unknown_mode(app, client, admin):
    resp = client.post(PARSE_URL,
                       json={"sql": SQLITE_SQL, "dialect": "sqlite", "mode": "weird"})
    assert resp.status_code == 400


def test_parse_rejects_bad_dialect(app, client, admin):
    resp = client.post(PARSE_URL, json={"sql": SQLITE_SQL, "dialect": "oracle"})
    assert resp.status_code == 400


def test_parse_empty_sql(app, client, admin):
    resp = client.post(PARSE_URL, json={"sql": "  ", "dialect": "sqlite"})
    assert resp.status_code == 400
    assert "CREATE TABLE" in resp.get_json()["error"]


def test_parse_with_matching_target_connection(app, client, admin):
    conn_id = _make_connection(app, "本地 MySQL", "mysql")
    mysql_sql = (
        "CREATE TABLE t (id INT PRIMARY KEY AUTO_INCREMENT, name VARCHAR(50) NOT NULL);"
    )
    resp = client.post(PARSE_URL, json={
        "sql": mysql_sql, "dialect": "mysql", "target_connection_id": conn_id,
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["table_count"] == 1


def test_parse_rejects_mismatched_target_connection(app, client, admin):
    conn_id = _make_connection(app, "本地 MySQL", "mysql")
    resp = client.post(PARSE_URL, json={
        "sql": SQLITE_SQL, "dialect": "sqlite", "target_connection_id": conn_id,
    })
    assert resp.status_code == 400
    assert "不一致" in resp.get_json()["error"]


def test_parse_missing_target_connection(app, client, admin):
    resp = client.post(PARSE_URL, json={
        "sql": SQLITE_SQL, "dialect": "sqlite", "target_connection_id": 999,
    })
    assert resp.status_code == 400
    assert "不存在" in resp.get_json()["error"]
