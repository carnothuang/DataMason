"""DR-08 验收：一键清空演示数据（种子行登记 + 精确删除）。

- 部署种子时逐行登记主键（deployment_seed_rows），清空时只删这些行；
- 用户后录的数据（含相同 created_by）不受影响；
- 逆拓扑序删除（orders 先于 customers），规避外键；
- 权限：创建人/管理员可清、操作员 403；直连模式无可清（400）；
- 清空写系统动作日志 action=seed，并把 deployments.seed_data 置 False。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from server.auth.service import hash_password
from server.deploy import service as deploy_service
from server.models import (
    Connection,
    Deployment,
    DeploymentMember,
    DeploymentSeedRow,
    User,
)

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

LINK_SPEC = {
    "biz_notes": "外部物件",
    "tables": [
        {
            "name": "things",
            "biz_name": "物件",
            "columns": [
                {"name": "id", "biz_name": "主键", "type": "INTEGER",
                 "is_pk": True, "autoincrement": True, "nullable": False},
                {"name": "name", "biz_name": "名称", "type": "VARCHAR", "length": 50},
            ],
            "indexes": [],
        }
    ],
}


class FakeLlm:
    def chat_json(self, system_prompt, user_prompt):
        class _Usage:
            tokens_in = tokens_out = duration_ms = 1

        return LINKED_SPEC, _Usage()


@pytest.fixture()
def patch_llm(monkeypatch):
    monkeypatch.setattr(
        "server.settings.service.get_default_llm_client",
        lambda _app: FakeLlm(),
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
        uid = u.id
    c = app.test_client()
    assert c.post("/api/v1/auth/login",
                  json={"username": "laomao", "password": "pw"}).status_code == 200
    return {"client": c, "id": uid}


def _session(app):
    return app.extensions["datamason_session_factory"]()


def _mk_user(app, username: str) -> int:
    with _session(app) as s:
        u = User(username=username, display_name=username,
                 password_hash=hash_password("pw"))
        s.add(u)
        s.commit()
        return u.id


def _login(app, username: str, password: str = "pw"):
    c = app.test_client()
    assert c.post("/api/v1/auth/login",
                  json={"username": username, "password": password}).status_code == 200
    return c


def _deploy(client, seed: bool = True) -> int:
    preview = client.post("/api/v1/deployments/preview",
                          json={"description": "进销存"}).get_json()
    return client.post("/api/v1/deployments/confirm", json={
        "spec_json": preview["spec_json"], "biz_name": "进销存",
        "seed_data": seed,
    }).get_json()["deployment_id"]


def _biz_uri(app, did: int) -> str:
    with _session(app) as s:
        d = s.get(Deployment, did)
        conn = s.get(Connection, d.connection_id)
        return app.extensions["cipher"].decrypt(conn.conn_uri)


def test_clear_seed_only_removes_seed_rows(app, env, patch_llm):
    c = env["client"]
    did = _deploy(c, seed=True)
    with _session(app) as s:
        assert s.query(DeploymentSeedRow).filter_by(deployment_id=did).count() == 20

    # 用户后录一条真实客户（与种子同 created_by=1，故只能靠主键区分）
    created = c.post(f"/api/v1/app/{did}/api/tables/customers/rows",
                     json={"name": "真实客户"})
    assert created.status_code == 201
    real_id = created.get_json()["id"]

    resp = c.post(f"/api/v1/deployments/{did}/seed/clear")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["deleted"] == 20 and body["failed"] == 0 and body["already_empty"] is False

    uri = _biz_uri(app, did)
    engine = create_engine(uri, future=True)
    try:
        with engine.connect() as conn_db:
            assert conn_db.execute(text("SELECT COUNT(*) FROM orders")).scalar() == 0
            rows = conn_db.execute(text("SELECT id, name FROM customers")).fetchall()
    finally:
        engine.dispose()
    assert rows == [(real_id, "真实客户")]  # 只剩用户真实数据

    with _session(app) as s:
        assert s.query(DeploymentSeedRow).filter_by(deployment_id=did).count() == 0
        assert s.get(Deployment, did).seed_data is False

    # 系统动作日志 action=seed
    logs = c.get(f"/api/v1/app/{did}/api/activity-logs?action=seed").get_json()["logs"]
    assert len(logs) == 1 and logs[0]["action"] == "seed"

    # 再次清空：无种子可清
    again = c.post(f"/api/v1/deployments/{did}/seed/clear").get_json()
    assert again["already_empty"] is True and again["deleted"] == 0


def test_clear_seed_permissions(app, env, patch_llm):
    did = _deploy(env["client"], seed=True)
    op_id = _mk_user(app, "op")
    adm_id = _mk_user(app, "adm")
    with _session(app) as s:
        s.add(DeploymentMember(deployment_id=did, user_id=op_id, role="operator"))
        s.add(DeploymentMember(deployment_id=did, user_id=adm_id, role="admin"))
        s.commit()

    assert _login(app, "op").post(
        f"/api/v1/deployments/{did}/seed/clear").status_code == 403
    # 管理员可清（数据管理职权）
    assert _login(app, "adm").post(
        f"/api/v1/deployments/{did}/seed/clear").status_code == 200


def test_clear_seed_link_mode_rejected(app, env, patch_llm):
    data_root = Path(app.config["DATA_ROOT"])
    data_root.mkdir(parents=True, exist_ok=True)
    ext_db = data_root / "dr08_external.db"
    con = sqlite3.connect(ext_db)
    try:
        con.executescript(
            "CREATE TABLE things (id INTEGER PRIMARY KEY AUTOINCREMENT, name VARCHAR(50));"
        )
        con.commit()
    finally:
        con.close()
    with _session(app) as s:
        conn = Connection(
            name="DR08外部库", dialect="sqlite",
            conn_uri=app.extensions["cipher"].encrypt(f"sqlite:///{ext_db.as_posix()}"),
        )
        s.add(conn)
        s.commit()
        conn_id = conn.id
        did = deploy_service.confirm(
            app, LINK_SPEC, biz_name="直连物件", data_mode="link",
            connection_id=conn_id, seed_data=True,
        )["deployment_id"]

    resp = env["client"].post(f"/api/v1/deployments/{did}/seed/clear")
    assert resp.status_code == 400
    assert "直连" in resp.get_json()["error"]
    # 外部库未被触碰
    assert ext_db.exists()
