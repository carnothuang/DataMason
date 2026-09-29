"""A3 验收：创建模式可落 MySQL/PostgreSQL（在既有服务器连接上建独立库）。

- 离线：URI 拆分 / 库名生成 / SQLite 目标被拒；
- 真库（env 门控）：DATAMASON_TEST_MYSQL_URL / DATAMASON_TEST_PG_URL 存在时，
  经 confirm(target_connection_id=…) 建库→建表→种子→回读，最后 DROP 清理。
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, inspect, text

from datamason.auth.service import hash_password
from datamason.deploy import service as deploy_service
from datamason.models import Connection, Deployment, User

SPEC = {
    "biz_notes": "留言板",
    "tables": [
        {
            "name": "messages",
            "biz_name": "留言",
            "columns": [
                {"name": "id", "biz_name": "主键", "type": "INTEGER",
                 "is_pk": True, "autoincrement": True, "nullable": False},
                {"name": "visitor", "biz_name": "访客", "type": "VARCHAR",
                 "length": 64, "nullable": False},
            ],
            "indexes": [],
        }
    ],
}

MYSQL_URL = os.environ.get("DATAMASON_TEST_MYSQL_URL")
PG_URL = os.environ.get("DATAMASON_TEST_PG_URL")


class FakeLlm:
    def chat_json(self, system_prompt, user_prompt):
        class _Usage:
            tokens_in = tokens_out = duration_ms = 1

        return SPEC, _Usage()


@pytest.fixture()
def patch_llm(monkeypatch):
    monkeypatch.setattr(
        "datamason.settings.service.get_default_llm_client",
        lambda _app: FakeLlm(),
    )
    yield


@pytest.fixture()
def env(app):
    with app.extensions["datamason_session_factory"]() as s:
        u = User(username="laomao", display_name="老猫",
                 password_hash=hash_password("pw"), is_platform_admin=True)
        s.add(u)
        s.commit()
    c = app.test_client()
    assert c.post("/api/v1/auth/login",
                  json={"username": "laomao", "password": "pw"}).status_code == 200
    return c


# ---------------- 离线 ----------------

def test_split_scheme_host_ignores_database():
    assert deploy_service._split_scheme_host(
        "mysql+pymysql://u:p@h:3306/db") == ("mysql+pymysql", "u:p@h:3306")
    assert deploy_service._split_scheme_host(
        "postgresql+psycopg2://u:p@h:5432") == ("postgresql+psycopg2", "u:p@h:5432")


def test_gen_db_name_is_identifier_safe():
    name = deploy_service._gen_db_name("客户与订单")
    assert name.startswith("dm_")
    assert name.replace("_", "").isalnum()


def test_create_target_database_rejects_sqlite(app):
    with app.extensions["datamason_session_factory"]() as s:
        c = Connection(name="本地", dialect="sqlite",
                       conn_uri=app.extensions["cipher"].encrypt("sqlite:///x.db"))
        s.add(c)
        s.commit()
        cid = c.id
    with app.extensions["datamason_session_factory"]() as s:
        host = s.get(Connection, cid)
        with pytest.raises(deploy_service.DeployError):
            deploy_service.create_target_database(app, host, "测试")


# ---------------- 真库（env 门控） ----------------

def _register_host(app, uri: str, dialect: str) -> int:
    with app.extensions["datamason_session_factory"]() as s:
        c = Connection(name=f"主机-{dialect}", dialect=dialect,
                       conn_uri=app.extensions["cipher"].encrypt(uri))
        s.add(c)
        s.commit()
        return c.id


def _confirm_target(app, client, host_id: int, biz: str) -> int:
    preview = client.post("/api/v1/deployments/preview",
                          json={"description": "留言板"}).get_json()
    resp = client.post("/api/v1/deployments/confirm", json={
        "spec_json": preview["spec_json"], "biz_name": biz, "seed_data": True,
        "target_connection_id": host_id,
    })
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()["deployment_id"]


def _biz_uri(app, did: int) -> str:
    with app.extensions["datamason_session_factory"]() as s:
        d = s.get(Deployment, did)
        conn = s.get(Connection, d.connection_id)
        return app.extensions["cipher"].decrypt(conn.conn_uri)


@pytest.mark.skipif(not MYSQL_URL, reason="需 DATAMASON_TEST_MYSQL_URL")
def test_create_mode_mysql_target(app, env, patch_llm):
    host_id = _register_host(app, MYSQL_URL, "mysql")
    did = _confirm_target(app, env, host_id, "A3MySQL")
    uri = _biz_uri(app, did)
    db_name = uri.rsplit("/", 1)[-1]
    engine = create_engine(uri, future=True)
    try:
        assert "messages" in inspect(engine).get_table_names()
        with engine.connect() as conn_db:
            assert conn_db.execute(text("SELECT COUNT(*) FROM messages")).scalar() == 10
    finally:
        engine.dispose()
        admin = create_engine(MYSQL_URL, future=True)
        try:
            with admin.begin() as conn_db:
                conn_db.execute(text(f"DROP DATABASE IF EXISTS {db_name}"))
        finally:
            admin.dispose()


@pytest.mark.skipif(not PG_URL, reason="需 DATAMASON_TEST_PG_URL")
def test_create_mode_pg_target(app, env, patch_llm):
    host_id = _register_host(app, PG_URL, "postgresql")
    did = _confirm_target(app, env, host_id, "A3PG")
    uri = _biz_uri(app, did)
    db_name = uri.rsplit("/", 1)[-1]
    engine = create_engine(uri, future=True)
    try:
        assert "messages" in inspect(engine).get_table_names()
        with engine.connect() as conn_db:
            assert conn_db.execute(text("SELECT COUNT(*) FROM messages")).scalar() == 10
    finally:
        engine.dispose()
        admin = create_engine(
            PG_URL, future=True).execution_options(isolation_level="AUTOCOMMIT")
        try:
            with admin.connect() as conn_db:
                conn_db.execute(text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))
        finally:
            admin.dispose()
