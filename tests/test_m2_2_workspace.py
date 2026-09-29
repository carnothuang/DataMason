"""M2-2 验收：工作台运行时 v0。

- GET /api/v1/app/<deployment_id> 返回菜单树（deployment_menus，含父子）+ 页面规格
- 菜单 ref_id → page_specs 闭环（B2）在接口层可见，前端可渲染
- 已部署（running）才可进入；不存在 404、未就绪 409、未登录 401
"""
from __future__ import annotations

import pytest

from datamason.auth.service import hash_password
from datamason.models import Deployment, DeploymentMenu, PageSpec, User

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
    def chat_json(self, system_prompt, user_prompt):
        class _Usage:
            tokens_in = 1
            tokens_out = 1
            duration_ms = 1

        return GUESTBOOK_SPEC, _Usage()


@pytest.fixture()
def admin(app, client):
    """建管理员并让共享 client 登录（业务接口需会话）。"""
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
        "datamason.settings.service.get_default_llm_client",
        lambda _app: FakeLlm(),
    )
    yield


def _deploy_one(client) -> int:
    preview = client.post("/api/v1/deployments/preview",
                          json={"description": "留言板"}).get_json()
    result = client.post("/api/v1/deployments/confirm", json={
        "spec_json": preview["spec_json"],
        "biz_name": "留言板",
        "seed_data": True,
    }).get_json()
    return result["deployment_id"]


def _session(app):
    return app.extensions["datamason_session_factory"]()


def test_workspace_requires_login(app, client, admin):
    anon = app.test_client()
    assert anon.get("/api/v1/app/1").status_code == 401


def test_workspace_unknown_deployment(app, client, admin):
    assert client.get("/api/v1/app/999").status_code == 404


def test_workspace_not_running_rejected(app, client, admin):
    with _session(app) as s:
        draft = Deployment(biz_name="草稿", data_mode="create", db_name="",
                           status="draft", owner_id=admin)
        s.add(draft)
        s.commit()
        did = draft.id
    resp = client.get(f"/api/v1/app/{did}")
    assert resp.status_code == 409
    assert "尚未就绪" in resp.get_json()["error"]


def test_workspace_info_menus_pages(app, client, admin, patch_llm):
    did = _deploy_one(client)
    resp = client.get(f"/api/v1/app/{did}")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()

    assert body["biz_name"] == "留言板"
    assert body["entry_path"] == f"/app/{did}"
    # 菜单：仅 crud_list 挂菜单，ref_id 指向 page_specs.id（闭环 B2）
    assert len(body["menus"]) == 1
    menu = body["menus"][0]
    assert menu["label"] == "留言管理"
    assert menu["kind"] == "table"
    assert menu["children"] == []
    # 页面规格：crud_list 解析出业务表
    assert len(body["pages"]) == 2
    crud = next(p for p in body["pages"] if p["kind"] == "crud_list")
    assert crud["table"] == "messages"
    assert crud["table_biz_name"] == "留言"
    assert menu["ref_id"] in {p["id"] for p in body["pages"]}
    public = next(p for p in body["pages"] if p["kind"] == "public_form")
    assert public["table"] == "messages"


def test_workspace_menu_tree_nested(app, client, admin, patch_llm):
    """手工排一棵父→子菜单，接口应返回嵌套树，父菜单 ref_id 为空。"""
    did = _deploy_one(client)
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        pages = s.query(PageSpec).filter_by(deployment_id=did).all()
        crud = next(p for p in pages if p.kind == "crud_list")
        parent = DeploymentMenu(deployment_id=did, label="运营", kind="group", ref_id=None)
        s.add(parent)
        s.flush()
        child = DeploymentMenu(deployment_id=did, parent_id=parent.id,
                               label="留言明细", kind="table", ref_id=crud.id)
        s.add(child)
        s.commit()

    body = client.get(f"/api/v1/app/{did}").get_json()
    assert len(body["menus"]) == 2  # 原有根 + 新父
    group = next(m for m in body["menus"] if m["label"] == "运营")
    assert group["ref_id"] is None
    assert group["children"][0]["label"] == "留言明细"
    assert group["children"][0]["ref_id"] in {p["id"] for p in body["pages"]}


def test_workspace_owner_written_at_deploy(app, client, admin, patch_llm):
    did = _deploy_one(client)
    with _session(app) as s:
        assert s.get(Deployment, did).owner_id == admin