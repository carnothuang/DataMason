"""M1-2 验收：创建模式部署管线（提案 → 确认 → 独立库/元数据/种子/入口）。

用 fake LLM 客户端注入确定性 spec，不触网；验证走查阻塞项 B1（创建模式自动建连接）
与 B2（菜单 ref_id→page_specs）在代码层闭环。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

from server.auth.service import hash_password
from server.models import (
    Connection,
    Deployment,
    DeploymentMenu,
    PageSpec,
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
def admin(app):
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        u = User(username="laomao", display_name="老猫",
                 password_hash=hash_password("pw"), is_platform_admin=True)
        s.add(u)
        s.commit()
        return u.id


@pytest.fixture()
def patch_llm(app, monkeypatch):
    import server.deploy.service as deploy_service

    monkeypatch.setattr(
        "server.settings.service.get_default_llm_client",
        lambda _app: FakeLlm(GUESTBOOK_SPEC),
    )
    # deploy 模块内若已 import 引用不影响（它在函数内 import），保险也 patch
    yield


def _session(app):
    return app.extensions["datamason_session_factory"]()


def test_preview_returns_proposal(app, client, admin, patch_llm):
    resp = client.post("/api/v1/deployments/preview",
                       json={"description": "做个留言板收集访客反馈"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["attempts"] == 1
    assert data["tables"][0]["name"] == "messages"
    # 归属列在提案可见（注入后）
    col_names = {c["name"] for c in data["tables"][0]["columns"]}
    assert {"created_by", "created_at", "updated_by", "updated_at"} <= col_names
    # messages 同时派生 member 管理页 + public 提交页
    kinds = {(p["table"], p["kind"], p["access"]) for p in data["pages"]}
    assert ("messages", "crud_list", "member") in kinds
    assert ("messages", "public_form", "public") in kinds
    assert "spec_json" in data


def test_preview_requires_description(app, client, admin, patch_llm):
    assert client.post("/api/v1/deployments/preview", json={}).status_code == 400


def test_confirm_full_pipeline(app, client, admin, patch_llm):
    preview = client.post("/api/v1/deployments/preview",
                          json={"description": "留言板"}).get_json()

    resp = client.post("/api/v1/deployments/confirm", json={
        "spec_json": preview["spec_json"],
        "biz_name": "留言板验证",
        "description": "做个留言板收集访客反馈",
        "seed_data": True,
    })
    assert resp.status_code == 201, resp.get_data(as_text=True)
    result = resp.get_json()
    assert result["status"] == "running"
    assert result["entry_path"] == f"/app/{result['deployment_id']}"
    assert result["seeded"] is True
    did = result["deployment_id"]

    with _session(app) as s:
        # 部署 + 自动登记连接（B1）
        d = s.get(Deployment, did)
        assert d.status == "running" and d.connection_id
        conn = s.get(Connection, d.connection_id)
        assert conn.dialect == "sqlite" and conn.name.startswith("[部署]")
        # 页面与菜单闭环（B2：菜单 ref_id 指向 page_specs.id）
        pages = s.query(PageSpec).filter_by(deployment_id=did).all()
        kinds = {p.kind for p in pages}
        assert kinds == {"crud_list", "public_form"}
        menus = s.query(DeploymentMenu).filter_by(deployment_id=did).all()
        assert len(menus) == 1  # 仅 crud_list 挂菜单
        page_ids = {p.id for p in pages}
        assert menus[0].ref_id in page_ids

    # 业务物理库真实存在且含归属列 + 10 行种子
    data_root = Path(app.config["DATA_ROOT"])
    db_files = list((data_root / "deployments").glob("*.db"))
    assert len(db_files) == 1
    biz = create_engine(f"sqlite:///{db_files[0].as_posix()}")
    cols = {c["name"] for c in inspect(biz).get_columns("messages")}
    assert {"created_by", "created_at", "updated_by", "updated_at"} <= cols
    with biz.connect() as conn_db:
        count = conn_db.execute(text("SELECT COUNT(*) FROM messages")).scalar()
        assert count == 10
        created_by = conn_db.execute(text(
            "SELECT DISTINCT created_by FROM messages")).fetchall()
    assert created_by == [(admin,)]  # 跨库身份对齐 users.id
    biz.dispose()

    # 清单接口
    listed = client.get("/api/v1/deployments").get_json()
    assert len(listed) == 1 and listed[0]["biz_name"] == "留言板验证"


def test_confirm_without_seed(app, client, admin, patch_llm):
    preview = client.post("/api/v1/deployments/preview",
                          json={"description": "留言板"}).get_json()
    result = client.post("/api/v1/deployments/confirm", json={
        "spec_json": preview["spec_json"],
        "biz_name": "空留言板",
        "seed_data": False,
    }).get_json()
    assert result["seeded"] is False
    with _session(app) as s:
        d = s.get(Deployment, result["deployment_id"])
        db_path = Path(app.config["DATA_ROOT"]) / d.db_name
    biz = create_engine(f"sqlite:///{db_path.as_posix()}")
    with biz.connect() as conn_db:
        assert conn_db.execute(text("SELECT COUNT(*) FROM messages")).scalar() == 0
    biz.dispose()


def test_confirm_without_user_rejected(app, client, patch_llm):
    preview = client.post("/api/v1/deployments/preview",
                          json={"description": "x"})
    # 无任何用户 → preview 仍可（只依赖模型）；但 confirm 因无 owner 报错
    # 先造模型场景：preview 会因默认 LLM fake 成功，confirm 无用户应 400
    if preview.status_code == 200:
        resp = client.post("/api/v1/deployments/confirm", json={
            "spec_json": preview.get_json()["spec_json"],
            "biz_name": "x",
        })
        assert resp.status_code == 400
        assert "向导" in resp.get_json()["error"]
