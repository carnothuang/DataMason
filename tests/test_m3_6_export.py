"""M3-6 验收：配置包导出（DR-03/§五）。

- GET /deployments/{id}/export：部署元信息 + 连接（脱敏）+ table_specs + page_specs +
  deployment_menus + crud_configs + flow_specs + 全量 committed activity_logs；
  JSON 附件（Content-Disposition），可离线打开（json.dumps 可序列化）。
- 权限：创建人/管理员可导出；操作员/非成员 403；匿名 401。
- 停止态也可导出；pending 日志不进入导出。
"""
from __future__ import annotations

import json

import pytest

from server.auth.service import hash_password
from server.models import ActivityLog, DeploymentMember, User

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


def test_export_package_creator_full_contents(app, env, patch_llm):
    did = _deploy(env["client"])
    itf = env["client"].get(f"/api/v1/deployments/{did}/interfaces").get_json()
    crud_page = next(p for p in itf["pages"] if p["kind"] == "crud_list")

    # 造一条流程（含 flow_specs）+ 业务数据增删改（含 committed 日志）
    env["client"].post(f"/api/v1/deployments/{did}/flows", json={
        "name": "入库流程",
        "steps": [{"name": "收货登记", "page_id": crud_page["id"],
                   "status_field": None}],
    })
    row = env["client"].post(f"/api/v1/app/{did}/api/tables/messages/rows",
                             json={"visitor": "甲", "content": "你好"}).get_json()
    env["client"].put(f"/api/v1/app/{did}/api/tables/messages/rows/{row['id']}",
                      json={"content": "改过"})
    env["client"].post(f"/api/v1/app/{did}/api/tables/messages/rows",
                       json={"visitor": "乙"}).get_json()

    resp = env["client"].get(f"/api/v1/deployments/{did}/export")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert "attachment" in resp.headers.get("Content-Disposition", "")
    assert f"datamason-deploy-{did}.json" in resp.headers["Content-Disposition"]
    pkg = resp.get_json()

    # 顶层结构 + 可离线打开（JSON 可序列化）
    assert pkg["format"] == "datamason.deployment.export" and pkg["version"] == 1
    json.dumps(pkg, ensure_ascii=False)

    d = pkg["deployment"]
    assert d["id"] == did and d["biz_name"] == "留言板"
    assert d["connection"] and d["connection"]["dialect"] == "sqlite"
    assert "conn_uri" not in d["connection"]  # 不泄露连接凭证
    assert d["owner"]["username"] == "laomao"

    assert len(pkg["table_specs"]) == 1 and pkg["table_specs"][0]["name"] == "messages"
    kinds = {p["kind"] for p in pkg["page_specs"]}
    assert {"crud_list", "public_form", "flow"} <= kinds  # 含流程页（M3-4 生成）
    assert pkg["deployment_menus"] and pkg["deployment_menus"][0]["ref_id"]
    assert pkg["crud_configs"]
    assert len(pkg["flow_specs"]) == 1
    assert pkg["flow_specs"][0]["steps"][0]["name"] == "收货登记"

    # 全量 committed 日志：含 deploy + 业务 create/update + flow
    logs = pkg["activity_logs"]
    assert len(logs) == pkg["counts"]["activity_logs"]
    actions = {(lg["action"], lg["object_table"]) for lg in logs}
    assert any(lg["action"] == "deploy" for lg in logs)
    assert ("create", "messages") in actions and ("update", "messages") in actions
    assert any(lg["action"] == "flow" for lg in logs)
    # 日志带可读参与者与变更
    upd = next(lg for lg in logs if lg["action"] == "update")
    assert upd["actor_user_id"] == env["id"] and upd["actor_display"] == "老猫"
    assert upd["changes"] and upd["status"] == "committed"


def test_export_excludes_pending_logs(app, env, patch_llm):
    did = _deploy(env["client"])
    with _session(app) as s:
        s.add(ActivityLog(
            deployment_id=did, action="create", status="pending",
            object_kind="table", object_table="messages", object_label="pending-行",
        ))
        s.commit()
    pkg = env["client"].get(f"/api/v1/deployments/{did}/export").get_json()
    assert all(lg["status"] == "committed" for lg in pkg["activity_logs"])
    assert all("pending-行" != lg["object_label"] for lg in pkg["activity_logs"])


def test_export_permission_and_stopped(app, env, patch_llm):
    did = _deploy(env["client"])
    # 操作员 403；管理员 200；非成员 403；匿名 401
    op_id = _mk_user(app, "op")
    adm_id = _mk_user(app, "adm")
    outsider_id = _mk_user(app, "outsider")
    with _session(app) as s:
        s.add(DeploymentMember(deployment_id=did, user_id=op_id, role="operator"))
        s.add(DeploymentMember(deployment_id=did, user_id=adm_id, role="admin"))
        s.commit()
    assert _login(app, "op").get(f"/api/v1/deployments/{did}/export").status_code == 403
    assert _login(app, "adm").get(f"/api/v1/deployments/{did}/export").status_code == 200
    assert _login(app, "outsider").get(f"/api/v1/deployments/{did}/export").status_code == 403
    assert app.test_client().get(f"/api/v1/deployments/{did}/export").status_code == 401

    # 停止态也可导出
    assert env["client"].post(f"/api/v1/deployments/{did}/stop").status_code == 200
    stopped = env["client"].get(f"/api/v1/deployments/{did}/export")
    assert stopped.status_code == 200, stopped.get_data(as_text=True)
    assert stopped.get_json()["deployment"]["status"] == "stopped"