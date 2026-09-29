"""M0-1 验收：惰性建表 + 健康检查 + 首次启动向导。"""
from __future__ import annotations

from sqlalchemy import create_engine, inspect

from datamason.auth.service import verify_password
from datamason.models import User


def test_meta_tables_created_lazy(app):
    """启动即建齐全部元数据表（checkfirst 幂等）。"""
    engine = app.extensions["datamason_engine"]
    names = set(inspect(engine).get_table_names())
    expected = {
        "users",
        "connections",
        "deployments",
        "table_specs",
        "page_specs",
        "crud_configs",
        "deployment_menus",
        "deployment_menu_grants",
        "deployment_members",
        "flow_specs",
        "activity_logs",
        "ai_generations",
        "llm_models",
    }
    assert expected <= names


def test_health(client):
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


def test_setup_status_initially_needs_setup(client):
    resp = client.get("/api/v1/auth/setup-status")
    data = resp.get_json()
    assert data["needs_setup"] is True
    assert data["user_count"] == 0


def test_bootstrap_without_env_is_noop(app, client):
    resp = client.post("/api/v1/auth/bootstrap")
    assert resp.get_json() == {"initialized": False, "reason": "env_missing"}
    assert client.get("/api/v1/auth/setup-status").get_json()["needs_setup"] is True


def test_bootstrap_creates_admin_from_env(tmp_path):
    from datamason.app import create_app
    from datamason.config import Config

    cfg = Config(
        meta_db_uri=f"sqlite:///{(tmp_path / 'm2.db').as_posix()}",
        admin_username="laomao",
        admin_password="secret123",
    )
    application = create_app(cfg)
    application.testing = True
    client = application.test_client()

    resp = client.post("/api/v1/auth/bootstrap")
    assert resp.get_json() == {"initialized": True, "reason": "admin_created"}

    status = client.get("/api/v1/auth/setup-status").get_json()
    assert status["needs_setup"] is False
    assert status["user_count"] == 1

    Session = application.extensions["datamason_session_factory"]
    with Session() as session:
        user = session.query(User).filter_by(username="laomao").one()
        assert user.is_platform_admin is True
        assert verify_password("secret123", user.password_hash)
        assert not verify_password("wrong", user.password_hash)

    # 幂等：再次 bootstrap 不再创建
    resp2 = client.post("/api/v1/auth/bootstrap")
    assert resp2.get_json() == {"initialized": False, "reason": "users_not_empty"}


def test_create_all_lazy_is_idempotent(app):
    from datamason.models import create_all_lazy

    create_all_lazy(app)  # 不应抛错
    create_all_lazy(app)
