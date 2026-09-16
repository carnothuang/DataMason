"""M3-7 验收：销毁（创建人独有，DR-14）。

- POST /deployments/{id}/destroy：二次确认/先导出由前端引导；后端负责清除。
- 创建模式：连独立 SQLite 物理库文件 + 自动连接一起清（限 DATA_ROOT 内才删）。
- 直连模式：绝不删外部库与连接，只清元数据（红线同 M2-7/M2-8）。
- 元数据级联清除：deployments/table_specs/page_specs/menus/grants/members/crud_configs/
  flow_specs/share_tokens/activity_logs 全清；销毁后清单不再显示、工作台 404、入口失效。
- 权限：仅创建人；管理员/操作员 403；匿名 401。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from server.auth.service import hash_password
from server.deploy import service as deploy_service
from server.models import (
    ActivityLog,
    AiGeneration,
    Connection,
    CrudConfig,
    Deployment,
    DeploymentApiKey,
    DeploymentMember,
    DeploymentMenu,
    DeploymentMenuGrant,
    DeploymentShareToken,
    FlowSpec,
    PageSpec,
    TableSpec,
    User,
)

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
            ],
            "indexes": [],
        }
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
            tokens_in = 1
            tokens_out = 1
            duration_ms = 1

        return GUESTBOOK_SPEC, _Usage()


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
        laomao_id = u.id
    c = app.test_client()
    assert c.post("/api/v1/auth/login",
                  json={"username": "laomao", "password": "pw"}).status_code == 200
    return {"client": c, "id": laomao_id}


def _mk_user(app, username: str) -> int:
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
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


def _session(app):
    return app.extensions["datamason_session_factory"]()


def _deploy(client) -> int:
    preview = client.post("/api/v1/deployments/preview",
                          json={"description": "留言板"}).get_json()
    return client.post("/api/v1/deployments/confirm", json={
        "spec_json": preview["spec_json"], "biz_name": "留言板", "seed_data": False,
    }).get_json()["deployment_id"]


def _counts(app, did: int) -> dict:
    with _session(app) as s:
        return {
            "deployment": s.query(Deployment).filter_by(id=did).count(),
            "tables": s.query(TableSpec).filter_by(deployment_id=did).count(),
            "pages": s.query(PageSpec).filter_by(deployment_id=did).count(),
            "menus": s.query(DeploymentMenu).filter_by(deployment_id=did).count(),
            "members": s.query(DeploymentMember).filter_by(deployment_id=did).count(),
            "cruds": s.query(CrudConfig).filter_by(deployment_id=did).count(),
            "flows": s.query(FlowSpec).filter_by(deployment_id=did).count(),
            "share": s.query(DeploymentShareToken).filter_by(deployment_id=did).count(),
            "api_keys": s.query(DeploymentApiKey).filter_by(deployment_id=did).count(),
            "generations": s.query(AiGeneration).filter_by(deployment_id=did).count(),
            "logs": s.query(ActivityLog).filter_by(deployment_id=did).count(),
        }


def test_destroy_create_mode_removes_db_metadata_and_logs(app, env, patch_llm):
    did = _deploy(env["client"])
    itf = env["client"].get(f"/api/v1/deployments/{did}/interfaces").get_json()
    crud_page = next(p for p in itf["pages"] if p["kind"] == "crud_list")
    # 造些数据：流程 + 分享链接 + 业务行（产生日志）
    env["client"].post(f"/api/v1/deployments/{did}/flows", json={
        "name": "入库流程", "steps": [{"name": "收货登记", "page_id": crud_page["id"]}]})
    env["client"].post(f"/api/v1/deployments/{did}/share-links", json={"role": "admin"})
    env["client"].post(f"/api/v1/deployments/{did}/api-keys", json={"name": "对接密钥"})
    env["client"].post(f"/api/v1/app/{did}/api/tables/messages/rows", json={"visitor": "甲"})
    with _session(app) as s:
        s.add(AiGeneration(kind="deploy", status="ok", deployment_id=did,
                           prompt_summary="销毁级联用例"))
        s.commit()

    with _session(app) as s:
        d = s.get(Deployment, did)
        conn_id = d.connection_id
        conn = s.get(Connection, conn_id)
        db_path = Path(
            app.extensions["cipher"].decrypt(conn.conn_uri).split("sqlite:///", 1)[-1]
        )
        assert db_path.exists()
        entry_path = d.entry_path
    before = _counts(app, did)
    assert before["tables"] == 1 and before["pages"] >= 2 and before["menus"] >= 1
    assert before["flows"] == 1 and before["share"] == 1 and before["logs"] >= 1
    assert before["api_keys"] == 1 and before["generations"] == 1

    resp = env["client"].post(f"/api/v1/deployments/{did}/destroy")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["destroyed"] is True and body["db_file_removed"] is True
    assert body["external_preserved"] is False

    after = _counts(app, did)
    assert all(v == 0 for v in after.values()), after
    assert not db_path.exists()  # 物理库文件已删
    with _session(app) as s:
        assert s.get(Connection, conn_id) is None  # 自动连接一并清
    # 清单不再显示 + 工作台 404 + 入口失效
    assert all(d["id"] != did for d in env["client"].get("/api/v1/deployments").get_json())
    assert env["client"].get(f"/api/v1/app/{did}").status_code == 404
    assert env["client"].get(
        f"/api/v1/entry/lookup?path={entry_path}").get_json()["found"] is False
    # 再次销毁 → 404（部署已不存在）
    assert env["client"].post(f"/api/v1/deployments/{did}/destroy").status_code == 404


def test_destroy_link_mode_preserves_external_db_and_connection(app, env, patch_llm):
    data_root = Path(app.config["DATA_ROOT"])
    data_root.mkdir(parents=True, exist_ok=True)
    ext_db = data_root / "m37_external.db"
    con = sqlite3.connect(ext_db)
    try:
        con.executescript(
            "CREATE TABLE things (id INTEGER PRIMARY KEY AUTOINCREMENT, name VARCHAR(50));"
            "INSERT INTO things (name) VALUES ('原型');"
        )
        con.commit()
    finally:
        con.close()

    with _session(app) as s:
        conn = Connection(
            name="M37外部库", dialect="sqlite",
            conn_uri=app.extensions["cipher"].encrypt(f"sqlite:///{ext_db.as_posix()}"),
        )
        s.add(conn)
        s.commit()
        conn_id = conn.id
        did = deploy_service.confirm(
            app, LINK_SPEC, biz_name="直连物件", data_mode="link",
            connection_id=conn_id, seed_data=False,
        )["deployment_id"]
    assert _counts(app, did)["tables"] == 1

    resp = env["client"].post(f"/api/v1/deployments/{did}/destroy")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["external_preserved"] is True and body["db_file_removed"] is False

    # 外部库文件与业务行原样保留；连接元数据保留（直连红线）
    assert ext_db.exists()
    con = sqlite3.connect(ext_db)
    try:
        assert con.execute("SELECT name FROM things").fetchall() == [("原型",)]
    finally:
        con.close()
    with _session(app) as s:
        assert s.get(Connection, conn_id) is not None
    assert all(v == 0 for v in _counts(app, did).values())


def test_destroy_creator_only(app, env, patch_llm):
    did = _deploy(env["client"])
    adm_id = _mk_user(app, "adm")
    op_id = _mk_user(app, "op")
    with _session(app) as s:
        s.add(DeploymentMember(deployment_id=did, user_id=adm_id, role="admin"))
        s.add(DeploymentMember(deployment_id=did, user_id=op_id, role="operator"))
        s.commit()
    assert _login(app, "adm").post(
        f"/api/v1/deployments/{did}/destroy").status_code == 403
    assert _login(app, "op").post(
        f"/api/v1/deployments/{did}/destroy").status_code == 403
    assert app.test_client().post(
        f"/api/v1/deployments/{did}/destroy").status_code == 401
    # 未被销毁
    assert _counts(app, did)["deployment"] == 1
    assert env["client"].post(
        f"/api/v1/deployments/{did}/destroy").status_code == 200