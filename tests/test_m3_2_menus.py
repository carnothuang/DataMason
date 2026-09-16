"""M3-2 验收：菜单组装与工作台布局（界面与菜单 工程室）。

- GET /deployments/{id}/interfaces：菜单树（平铺含排序）+ 页面卡 + 已授权菜单 id；创建人独有（DR-14）
- PUT /deployments/{id}/menus：整组保存（增删/排序/父子，parent_id 支持既有 id 与 -1/-2 新建引用），
  变更走 structure 埋点；复用 id 稳定（拖拽后刷新保持）
- 新建菜单默认不进操作员授权集合；创建人授权后才可见（M2-5 约束）
- PUT /deployments/{id}/entry_path：自定义路径 /guestbook（DR-15），留空恢复 /app/{id}；
  /api/v1/entry/lookup 解析新路径，旧路径 /app/{id} 不再暴露；创建人独有；非法/占用 400
- PUT /deployments/{id}/pages/{page_id}：界面卡「调整」改标题
"""
from __future__ import annotations

import pytest

from server.auth.service import hash_password
from server.models import ActivityLog, Deployment, DeploymentMember, DeploymentMenu, User

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


def _deploy(client) -> int:
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


def _menus(app, did: int) -> list[dict]:
    with _session(app) as s:
        rows = (
            s.query(DeploymentMenu)
            .filter_by(deployment_id=did)
            .order_by(DeploymentMenu.order_index, DeploymentMenu.id)
            .all()
        )
        return [{"id": m.id, "label": m.label, "ref_id": m.ref_id,
                 "parent_id": m.parent_id, "kind": m.kind, "order": m.order_index}
                for m in rows]


def _logs(app, did: int, action: str, object_kind: str = "") -> list[dict]:
    with _session(app) as s:
        q = s.query(ActivityLog).filter(
            ActivityLog.deployment_id == did,
            ActivityLog.action == action,
            ActivityLog.status == "committed",
        )
        if object_kind:
            q = q.filter(ActivityLog.object_kind == object_kind)
        return [{"object_label": r.object_label, "changes": r.changes}
                for r in q.order_by(ActivityLog.id).all()]


def test_deploy_creates_default_menus_in_order(app, env, patch_llm):
    did = _deploy(env["client"])
    menus = _menus(app, did)
    assert len(menus) >= 1 and all(m["kind"] == "table" for m in menus)
    assert [m["order"] for m in menus] == list(range(len(menus)))  # 顺序号 0..n-1
    # 初始不授任何菜单 → 操作员看不到新菜单（M2-5 约束）
    payload = env["client"].get(f"/api/v1/deployments/{did}/interfaces").get_json()
    assert payload["granted_menu_ids"] == []
    assert len(payload["pages"]) == 2  # 留言管理 crud_list + 我要留言 public_form


def test_interfaces_creator_only(app, env, patch_llm):
    did = _deploy(env["client"])
    adm_id = _mk_user(app, "adm")
    with _session(app) as s:
        s.add(DeploymentMember(deployment_id=did, user_id=adm_id, role="admin"))
        s.commit()
    adm = _login(app, "adm")
    assert adm.get(f"/api/v1/deployments/{did}/interfaces").status_code == 403
    assert adm.put(f"/api/v1/deployments/{did}/menus",
                   json={"nodes": []}).status_code == 403
    assert env["client"].get("/api/v1/deployments/999/interfaces").status_code == 404


def test_menu_layout_save_group_move_delete_keeps_ids(app, env, patch_llm):
    did = _deploy(env["client"])
    payload = env["client"].get(f"/api/v1/deployments/{did}/interfaces").get_json()
    (msg_menu,) = payload["menus"]
    msg_page = next(p for p in payload["pages"] if p["table"] == "messages"
                    and p["kind"] == "crud_list")

    # ① 一条请求：新建分组「访客区」（-1 引用）+ 把既有菜单移进去 + 新建「消息中心」挂在分组下
    nodes = [
        {"id": msg_menu["id"], "label": "留言管理", "kind": "table",
         "ref_id": msg_page["id"], "parent_id": None},
        {"label": "访客区", "kind": "table", "ref_id": None, "parent_id": None},
        {"label": "消息中心", "kind": "table", "ref_id": msg_page["id"],
         "parent_id": -2},  # -1=列表第一节点(留言管理)；-2=访客区（新建分组当日收纳子节点）
    ]
    resp = env["client"].put(f"/api/v1/deployments/{did}/menus", json={"nodes": nodes})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    saved = resp.get_json()["nodes"]
    assert saved[0]["id"] == msg_menu["id"]  # 既有菜单 id 保持稳定（刷新按 id 续用）
    group = saved[1]
    child = saved[2]
    assert child["parent_id"] == group["id"]

    menus = _menus(app, did)
    by_id = {m["id"]: m for m in menus}
    assert by_id[msg_menu["id"]]["order"] == 0
    assert by_id[group["id"]]["order"] == 1 and by_id[group["id"]]["ref_id"] is None
    assert by_id[child["id"]]["parent_id"] == group["id"] and by_id[child["id"]]["order"] == 2
    assert len(menus) == 3

    # ② 再拖拽：把「消息中心」挪到分组外顶层（引用分组 id 为父改为 None）
    nodes2 = [
        {"id": by_id[msg_menu["id"]]["id"], "label": "留言管理", "kind": "table",
         "ref_id": msg_page["id"], "parent_id": None},
        {"id": group["id"], "label": "访客区", "kind": "table", "ref_id": None,
         "parent_id": None},
        {"id": child["id"], "label": "消息中心", "kind": "table",
         "ref_id": msg_page["id"], "parent_id": None},
    ]
    resp2 = env["client"].put(f"/api/v1/deployments/{did}/menus", json={"nodes": nodes2})
    assert resp2.status_code == 200, resp2.get_data(as_text=True)
    saved2 = resp2.get_json()["nodes"]
    assert all(n["parent_id"] is None for n in saved2)

    # ③ 删除「访客区」（分组）
    nodes3 = [n for n in nodes2 if n["id"] != group["id"]]
    resp3 = env["client"].put(f"/api/v1/deployments/{did}/menus", json={"nodes": nodes3})
    assert resp3.status_code == 200
    assert len(_menus(app, did)) == 2

    # ④ structure 埋点：至少 3 条 committed（新建收纳 / 重排 / 删除）
    structure_logs = _logs(app, did, "structure", object_kind="menu")
    assert len(structure_logs) >= 3
    assert structure_logs[-1]["changes"]["removed"] == ["访客区"]


def test_new_menu_hidden_from_operator_until_granted(app, env, patch_llm):
    did = _deploy(env["client"])
    payload = env["client"].get(f"/api/v1/deployments/{did}/interfaces").get_json()
    (msg_menu,) = payload["menus"]
    msg_page = next(p for p in payload["pages"] if p["table"] == "messages"
                    and p["kind"] == "crud_list")
    op_id = _mk_user(app, "op")
    env["client"].post(f"/api/v1/deployments/{did}/members",
                       json={"username": "op", "role": "operator",
                             "menu_ids": [msg_menu["id"]]})
    op = _login(app, "op")

    def _op_menus():
        body = op.get(f"/api/v1/app/{did}").get_json()
        return [(m["label"], m["ref_id"]) for m in body["menus"]]

    assert _op_menus() == [("留言管理", msg_page["id"])]

    # 创建人新建菜单 → 操作员仍只见授权集合
    env["client"].put(f"/api/v1/deployments/{did}/menus", json={"nodes": [
        {"id": msg_menu["id"], "label": "留言管理", "kind": "table",
         "ref_id": msg_page["id"], "parent_id": None},
        {"label": "消息中心", "kind": "table", "ref_id": msg_page["id"],
         "parent_id": None},
    ]})
    assert _op_menus() == [("留言管理", msg_page["id"])]
    payload2 = env["client"].get(f"/api/v1/deployments/{did}/interfaces").get_json()
    new_menu = next(m for m in payload2["menus"]
                    if m["label"] == "消息中心")
    assert new_menu["id"] not in payload2["granted_menu_ids"]  # 界面明示「需授权」

    # 创建人授权后 → 操作员可见
    env["client"].put(
        f"/api/v1/deployments/{did}/members/{op_id}",
        json={"role": "operator", "menu_ids": [msg_menu["id"], new_menu["id"]]})
    labels = [m for m, _ in _op_menus()]
    assert "消息中心" in labels and "留言管理" in labels


def test_menu_save_validation(app, env, patch_llm):
    did = _deploy(env["client"])
    (menu,) = env["client"].get(f"/api/v1/deployments/{did}/interfaces").get_json()["menus"]
    assert env["client"].put(f"/api/v1/deployments/{did}/menus", json={"nodes": [
        {"id": menu["id"], "label": "", "kind": "table",
         "ref_id": menu["ref_id"], "parent_id": None},
    ]}).status_code == 400  # 空名称
    assert env["client"].put(f"/api/v1/deployments/{did}/menus", json={"nodes": [
        {"id": 999999, "label": "不存在", "kind": "table",
         "ref_id": None, "parent_id": None},
    ]}).status_code == 400  # 不属于该部署的 id
    assert env["client"].put(f"/api/v1/deployments/{did}/menus", json={"nodes": [
        {"label": "A", "kind": "table", "ref_id": None, "parent_id": -1},
    ]}).status_code == 400  # -1 越界（列表里只有自己）


def test_entry_path_custom_persist_reset_and_lookup(app, env, patch_llm):
    did = _deploy(env["client"])
    admin_user = env["id"]
    assert env["client"].get(f"/api/v1/entry/lookup?path=/app/{did}").get_json()["found"]
    assert env["client"].get("/api/v1/entry/lookup?path=/guestbook").get_json()["found"] is False

    # 设自定义路径 → 新路径可解析、旧路径 /app/{id} 不再暴露
    resp = env["client"].put(f"/api/v1/deployments/{did}/entry_path",
                             json={"path": "/guestbook"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["entry_path"] == "/guestbook"
    with _session(app) as s:
        assert s.get(Deployment, did).entry_path == "/guestbook"
    hit = env["client"].get("/api/v1/entry/lookup?path=/guestbook").get_json()
    assert hit["found"] and hit["deployment_id"] == did and hit["biz_name"] == "留言板"
    assert env["client"].get(f"/api/v1/entry/lookup?path=/app/{did}").get_json()["found"] is False
    # 内部 API 命名空间仍在（前端靠 /api/v1/app/{id} 加载数据；浏览器 URL 走解析）
    assert env["client"].get(f"/api/v1/app/{did}").status_code == 200

    # 留空恢复默认 /app/{id}
    resp = env["client"].put(f"/api/v1/deployments/{did}/entry_path", json={"path": ""})
    assert resp.get_json()["entry_path"] == f"/app/{did}"
    assert env["client"].get(f"/api/v1/entry/lookup?path=/app/{did}").get_json()["found"]
    assert env["client"].get("/api/v1/entry/lookup?path=/guestbook").get_json()["found"] is False

    # 埋点：structure/object_kind=deployment 至少一次
    assert any("入口路径调整" in lg["object_label"]
               for lg in _logs(app, did, "structure", object_kind="deployment"))


def test_entry_path_validation_and_creator_only(app, env, patch_llm):
    did = _deploy(env["client"])
    # 非法路径
    for bad in ["guestbook", "/a b", "/login", "/api/x", "//ab", "/a?b"]:
        assert env["client"].put(
            f"/api/v1/deployments/{did}/entry_path",
            json={"path": bad}).status_code == 400, bad
    # 占用
    did2 = _deploy(env["client"])
    assert env["client"].put(f"/api/v1/deployments/{did}/entry_path",
                             json={"path": "/shop"}).status_code == 200
    assert env["client"].put(f"/api/v1/deployments/{did2}/entry_path",
                             json={"path": "/shop"}).status_code == 400

    # 管理员 403（创建人独有 DR-14）
    adm_id = _mk_user(app, "adm")
    with _session(app) as s:
        s.add(DeploymentMember(deployment_id=did, user_id=adm_id, role="admin"))
        s.commit()
    adm = _login(app, "adm")
    assert adm.put(f"/api/v1/deployments/{did}/entry_path",
                   json={"path": "/other"}).status_code == 403
    # 未登录 401
    anon = app.test_client()
    assert anon.put(f"/api/v1/deployments/{did}/entry_path",
                    json={"path": "/anonymous"}).status_code == 401

    body = anon.get("/api/v1/entry/lookup?path=/shop").get_json()
    assert "found" not in body  # 匿名一律 401，入口解析仅限成员（不泄露部署存在性）


def test_rename_page_title_and_hidden_from_non_creator(app, env, patch_llm):
    did = _deploy(env["client"])
    payload = env["client"].get(f"/api/v1/deployments/{did}/interfaces").get_json()
    (msg_page,) = [p for p in payload["pages"] if p["kind"] == "crud_list"]

    resp = env["client"].put(f"/api/v1/deployments/{did}/pages/{msg_page['id']}",
                             json={"title": "留言管理中心"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    payload2 = env["client"].get(f"/api/v1/deployments/{did}/interfaces").get_json()
    assert next(p for p in payload2["pages"] if p["id"] == msg_page["id"])["title"] == "留言管理中心"
    # 工作台标题同步（page_specs 是单一数据源）
    ws = env["client"].get(f"/api/v1/app/{did}").get_json()
    assert any(p["title"] == "留言管理中心" for p in ws["pages"])

    assert any("界面标题调整" in lg["object_label"]
               for lg in _logs(app, did, "structure", object_kind="page"))
    # 非创建人 403
    adm_id = _mk_user(app, "adm")
    with _session(app) as s:
        s.add(DeploymentMember(deployment_id=did, user_id=adm_id, role="admin"))
        s.commit()
    adm = _login(app, "adm")
    assert adm.put(f"/api/v1/deployments/{did}/pages/{msg_page['id']}",
                   json={"title": "X"}).status_code == 403