"""M3-5 验收：访客公开页面 + 分享链接绑定身份（DR-04）。

- 访客公开页：`page_specs.access=public`+`kind=public_form` 匿名可读/可提交（走 CRUD 写路径）；
  归属列记系统（created_by=0），提交进 activity_logs（action=create, actor_user_id=NULL 显示「系统」）；
  匿名看不到任何菜单与数据（/app/{id} 401）；非公开页/缺字段/停止态分别 400/400/409。
- 分享链接：创建人独有（DR-14）生成/列表/撤销；任一登录用户 accept → 绑定为该部署成员
  （按档位与操作员功能集合），并写 member 日志；无效/已撤销 token → 404。
"""
from __future__ import annotations

import pytest

from server.auth.service import hash_password
from server.models import (
    ActivityLog,
    DeploymentMember,
    DeploymentMenu,
    DeploymentMenuGrant,
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
    result = client.post("/api/v1/deployments/confirm", json={
        "spec_json": preview["spec_json"],
        "biz_name": "留言板",
        "seed_data": False,
    }).get_json()
    return result["deployment_id"]


def _pages(env, did: int) -> list[dict]:
    return env["client"].get(f"/api/v1/deployments/{did}/interfaces").get_json()["pages"]


def _public_page(env, did: int) -> dict:
    return next(p for p in _pages(env, did) if p["kind"] == "public_form")


def test_public_page_anonymous_access_and_submit(app, env, patch_llm):
    did = _deploy(env["client"])
    page = _public_page(env, did)
    anon = app.test_client()

    # 匿名读公开表单元数据
    meta = anon.get(f"/api/v1/public/{did}/pages/{page['id']}")
    assert meta.status_code == 200, meta.get_data(as_text=True)
    body = meta.get_json()
    assert body["biz_name"] == "留言板" and body["kind"] == "public_form"
    assert {f["name"] for f in body["fields"]} == {"visitor", "content"}  # 不含 id/归属列

    # 非公开页 → 400
    crud_page = next(p for p in _pages(env, did) if p["kind"] == "crud_list")
    assert anon.get(f"/api/v1/public/{did}/pages/{crud_page['id']}").status_code == 400
    # 不存在 → 404
    assert anon.get(f"/api/v1/public/{did}/pages/999999").status_code == 404

    # 必填缺失 → 400
    bad = anon.post(f"/api/v1/public/{did}/pages/{page['id']}/submit",
                    json={"content": "无访客名"})
    assert bad.status_code == 400 and "访客" in bad.get_json()["error"]

    # 匿名提交成功
    ok = anon.post(f"/api/v1/public/{did}/pages/{page['id']}/submit",
                   json={"visitor": "匿名访客", "content": "你好"})
    assert ok.status_code == 201, ok.get_data(as_text=True)

    # 匿名看不到菜单与数据
    assert anon.get(f"/api/v1/app/{did}").status_code == 401
    assert anon.get(f"/api/v1/app/{did}/api/tables/messages/rows").status_code == 401

    # 创建人可见该行，归属列=系统(0)
    rows = env["client"].get(
        f"/api/v1/app/{did}/api/tables/messages/rows").get_json()["data"]
    row = next(r for r in rows if r["visitor"] == "匿名访客" and r["content"] == "你好")
    assert row["created_by"] == 0

    # 提交进 activity_logs（actor_user_id=NULL → 显示「系统」）
    logs = env["client"].get(
        f"/api/v1/app/{did}/api/activity-logs").get_json()["logs"]
    entry = next(lg for lg in logs
                 if lg["action"] == "create" and lg["object_table"] == "messages"
                 and lg["actor_user_id"] is None)
    assert entry["actor_display"] == "系统"
    with _session(app) as s:
        committed = (
            s.query(ActivityLog)
            .filter(ActivityLog.deployment_id == did,
                    ActivityLog.action == "create",
                    ActivityLog.actor_user_id.is_(None),
                    ActivityLog.status == "committed")
            .count()
        )
        assert committed == 1


def test_public_page_stopped_deployment(app, env, patch_llm):
    did = _deploy(env["client"])
    page = _public_page(env, did)
    anon = app.test_client()
    assert anon.get(f"/api/v1/public/{did}/pages/{page['id']}").status_code == 200
    assert env["client"].post(f"/api/v1/deployments/{did}/stop").status_code == 200
    assert anon.get(f"/api/v1/public/{did}/pages/{page['id']}").status_code == 409
    assert anon.post(f"/api/v1/public/{did}/pages/{page['id']}/submit",
                     json={"visitor": "x"}).status_code == 409


def test_share_link_create_creator_only_and_accept_operator(app, env, patch_llm):
    did = _deploy(env["client"])
    menus = env["client"].get(f"/api/v1/deployments/{did}/interfaces").get_json()["menus"]
    msg_menu = menus[0]

    # 管理员生成 → 403（创建人独有 DR-14）
    adm_id = _mk_user(app, "adm")
    with _session(app) as s:
        s.add(DeploymentMember(deployment_id=did, user_id=adm_id, role="admin"))
        s.commit()
    adm = _login(app, "adm")
    assert adm.post(f"/api/v1/deployments/{did}/share-links",
                    json={"role": "operator", "menu_ids": [msg_menu["id"]]}).status_code == 403
    assert adm.get(f"/api/v1/deployments/{did}/share-links").status_code == 403

    # 创建人生成操作员链接
    resp = env["client"].post(f"/api/v1/deployments/{did}/share-links",
                              json={"role": "operator", "menu_ids": [msg_menu["id"]]})
    assert resp.status_code == 201, resp.get_data(as_text=True)
    token = resp.get_json()["token"]

    # 列表 + 菜单 + 公开页（供前端展示）
    listing = env["client"].get(f"/api/v1/deployments/{did}/share-links").get_json()
    assert len(listing["links"]) == 1 and listing["links"][0]["role"] == "operator"
    assert listing["menus"] and listing["public_pages"]

    # 操作员至少一个菜单（生成时校验）
    assert env["client"].post(f"/api/v1/deployments/{did}/share-links",
                              json={"role": "operator", "menu_ids": []}).status_code == 400
    # 非本部署菜单
    assert env["client"].post(f"/api/v1/deployments/{did}/share-links",
                              json={"role": "operator", "menu_ids": [999999]}).status_code == 400

    # 新用户接受链接 → 绑定为操作员，只见授权菜单
    xiaoli_id = _mk_user(app, "xiaoli")
    xiaoli = _login(app, "xiaoli")
    acc = xiaoli.post(f"/api/v1/share/{token}/accept")
    assert acc.status_code == 200, acc.get_data(as_text=True)
    assert acc.get_json()["role"] == "operator"
    assert acc.get_json()["deployment_id"] == did
    ws = xiaoli.get(f"/api/v1/app/{did}").get_json()
    assert [m["label"] for m in ws["menus"]] == [msg_menu["label"]]
    with _session(app) as s:
        member = s.query(DeploymentMember).filter_by(
            deployment_id=did, user_id=xiaoli_id).first()
        assert member is not None and member.role == "operator"
        grants = s.query(DeploymentMenuGrant).filter_by(user_id=xiaoli_id).all()
        assert [g.menu_id for g in grants] == [msg_menu["id"]]

    # accept 幂等（再次接受不报错）
    assert xiaoli.post(f"/api/v1/share/{token}/accept").status_code == 200

    # 无效 token → 404；撤销后 → 404
    assert xiaoli.post("/api/v1/share/nope-token/accept").status_code == 404
    link_id = listing["links"][0]["id"]
    assert env["client"].delete(
        f"/api/v1/deployments/{did}/share-links/{link_id}").status_code == 200
    assert xiaoli.post(f"/api/v1/share/{token}/accept").status_code == 404

    # member 日志：生成 / 接受 / 撤销
    with _session(app) as s:
        share_logs = (
            s.query(ActivityLog)
            .filter(ActivityLog.deployment_id == did,
                    ActivityLog.action == "member",
                    ActivityLog.object_kind == "share")
            .all()
        )
        assert len(share_logs) >= 2
        assert any("通过分享链接加入" in (l.object_label or "") for l in
                   s.query(ActivityLog)
                   .filter(ActivityLog.deployment_id == did,
                           ActivityLog.action == "member",
                           ActivityLog.object_pk == str(xiaoli_id))
                   .all())


def test_share_link_admin_binding_sees_all_menus(app, env, patch_llm):
    did = _deploy(env["client"])
    resp = env["client"].post(f"/api/v1/deployments/{did}/share-links",
                              json={"role": "admin"})
    assert resp.status_code == 201, resp.get_data(as_text=True)
    token = resp.get_json()["token"]

    _mk_user(app, "wang")
    wang = _login(app, "wang")
    acc = wang.post(f"/api/v1/share/{token}/accept")
    assert acc.status_code == 200 and acc.get_json()["role"] == "admin"
    ws = wang.get(f"/api/v1/app/{did}").get_json()
    assert len(ws["menus"]) >= 1 and ws["role"] == "admin"

    # 创建人仍可生成链接（不受影响）
    assert env["client"].post(f"/api/v1/deployments/{did}/share-links",
                              json={"role": "operator", "menu_ids": [
                                  env["client"].get(
                                      f"/api/v1/deployments/{did}/interfaces"
                                  ).get_json()["menus"][0]["id"]]}).status_code == 201