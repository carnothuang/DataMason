"""M4-3 验收：钉住报表（dashboard 页）。

- 钉住：POST /deployments/{id}/qa/pin（创建人/管理员）→ 写 page_specs(kind=dashboard)
  + 挂 deployment_menus(kind=dashboard)；SQL 重新过只读门/白名单；同一 SQL 幂等；
  写 structure 埋点。
- 运行态重跑：GET /app/{id}/dashboards/{page_id} → 最新行集 + 图表规格；只读门复检；
  操作员须被授权挂载该报表的菜单（DR-02）否则 403。
- 取消钉住：DELETE /deployments/{id}/qa/pins/{page_id} → 页面/菜单/菜单授权一并清理。
- 权限：操作员不能钉住（403）；导出配置包含 dashboard 页（M3-6 级联沿用）。
"""
from __future__ import annotations

import pytest

from datamason.auth.service import hash_password
from datamason.models import (
    ActivityLog,
    DeploymentMember,
    DeploymentMenu,
    DeploymentMenuGrant,
    PageSpec,
    User,
)

SPEC = {
    "biz_notes": "留言板",
    "tables": [
        {
            "name": "messages",
            "biz_name": "留言",
            "columns": [
                {"name": "id", "biz_name": "主键", "type": "INTEGER",
                 "is_pk": True, "autoincrement": True, "nullable": False},
                {"name": "name", "biz_name": "姓名", "type": "VARCHAR",
                 "length": 64, "nullable": False},
                {"name": "amount", "biz_name": "金额", "type": "INTEGER", "nullable": True},
            ],
            "indexes": [],
        }
    ],
}

GOOD_SQL = "SELECT name, amount FROM messages ORDER BY amount DESC"
GOOD_CHART = {"type": "bar", "x": "name", "y": "amount"}


@pytest.fixture()
def env(app):
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        u = User(username="laomao", display_name="老猫",
                 password_hash=hash_password("pw"), is_platform_admin=True)
        s.add(u)
        s.commit()
    c = app.test_client()
    assert c.post("/api/v1/auth/login",
                  json={"username": "laomao", "password": "pw"}).status_code == 200
    return {"client": c}


def _session(app):
    return app.extensions["datamason_session_factory"]()


def _mk_user(app, username: str) -> int:
    with _session(app) as s:
        u = User(username=username, display_name=username,
                 password_hash=hash_password("pw"))
        s.add(u)
        s.commit()
        return u.id


def _login(app, username: str):
    c = app.test_client()
    assert c.post("/api/v1/auth/login",
                  json={"username": username, "password": "pw"}).status_code == 200
    return c


def _deploy(client) -> int:
    resp = client.post("/api/v1/deployments/confirm", json={
        "spec_json": SPEC, "biz_name": "钉住演示", "seed_data": True,
    })
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()["deployment_id"]


def _pin(client, did: int, sql=GOOD_SQL, chart=GOOD_CHART, title="金额排行"):
    return client.post(f"/api/v1/deployments/{did}/qa/pin",
                       json={"title": title, "sql": sql, "chart": chart,
                             "question": "金额排行"})


def test_pin_creates_dashboard_page_and_menu(app, env):
    did = _deploy(env["client"])
    resp = _pin(env["client"], did)
    assert resp.status_code == 201, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["duplicate"] is False and body["menu_id"]

    with _session(app) as s:
        page = s.get(PageSpec, body["page_id"])
        assert page.kind == "dashboard" and page.access == "member"
        assert page.title == "金额排行" and page.table_id is None
        layout = page.layout_json
        assert "messages" in layout and "bar" in layout
        menu = s.get(DeploymentMenu, body["menu_id"])
        assert menu.kind == "dashboard" and menu.ref_id == page.id
        # structure 埋点
        logs = (
            s.query(ActivityLog)
            .filter_by(deployment_id=did, action="structure", status="committed")
            .all()
        )
        assert any("钉住报表" in lg.object_label for lg in logs)

    # 幂等：同一 SQL 再钉一次不新建
    again = _pin(env["client"], did).get_json()
    assert again["duplicate"] is True and again["page_id"] == body["page_id"]
    with _session(app) as s:
        assert s.query(PageSpec).filter_by(deployment_id=did, kind="dashboard").count() == 1


def test_pin_readonly_gate(app, env):
    did = _deploy(env["client"])
    for bad in ("UPDATE messages SET amount=0", "SELECT * FROM other.messages",
                "SELECT 1; DROP TABLE messages"):
        resp = _pin(env["client"], did, sql=bad)
        assert resp.status_code == 400, (bad, resp.get_data(as_text=True))
        assert "只读门" in resp.get_json()["error"]
    with _session(app) as s:
        assert s.query(PageSpec).filter_by(deployment_id=did, kind="dashboard").count() == 0


def test_dashboard_run_returns_latest_rows(app, env):
    did = _deploy(env["client"])
    page_id = _pin(env["client"], did).get_json()["page_id"]

    run = env["client"].get(f"/api/v1/app/{did}/dashboards/{page_id}")
    assert run.status_code == 200, run.get_data(as_text=True)
    body = run.get_json()
    assert body["row_count"] == 10 and body["columns"] == ["name", "amount"]
    assert body["chart"] == GOOD_CHART and "messages" in body["sql"]

    # 新增一条 → 重跑数据为最新
    env["client"].post(f"/api/v1/app/{did}/api/tables/messages/rows",
                       json={"name": "新留言", "amount": 9999})
    run2 = env["client"].get(f"/api/v1/app/{did}/dashboards/{page_id}").get_json()
    assert run2["row_count"] == 11
    assert run2["rows"][0]["amount"] == 9999  # ORDER BY amount DESC


def test_dashboard_operator_needs_grant(app, env):
    did = _deploy(env["client"])
    body = _pin(env["client"], did).get_json()
    page_id, menu_id = body["page_id"], body["menu_id"]
    op_id = _mk_user(app, "op")
    with _session(app) as s:
        s.add(DeploymentMember(deployment_id=did, user_id=op_id, role="operator"))
        s.commit()

    op = _login(app, "op")
    # 未授权 → 403
    assert op.get(f"/api/v1/app/{did}/dashboards/{page_id}").status_code == 403
    # 操作员不能钉住
    assert op.post(f"/api/v1/deployments/{did}/qa/pin",
                   json={"title": "x", "sql": GOOD_SQL}).status_code == 403

    # 授权该报表菜单后 → 200
    with _session(app) as s:
        s.add(DeploymentMenuGrant(menu_id=menu_id, user_id=op_id))
        s.commit()
    assert op.get(f"/api/v1/app/{did}/dashboards/{page_id}").status_code == 200


def test_unpin_cleans_page_menu_and_grants(app, env):
    did = _deploy(env["client"])
    body = _pin(env["client"], did).get_json()
    page_id, menu_id = body["page_id"], body["menu_id"]
    op_id = _mk_user(app, "op")
    with _session(app) as s:
        s.add(DeploymentMenuGrant(menu_id=menu_id, user_id=op_id))
        s.commit()

    resp = env["client"].delete(f"/api/v1/deployments/{did}/qa/pins/{page_id}")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    with _session(app) as s:
        assert s.get(PageSpec, page_id) is None
        assert s.get(DeploymentMenu, menu_id) is None
        assert s.query(DeploymentMenuGrant).filter_by(menu_id=menu_id).count() == 0
        logs = s.query(ActivityLog).filter_by(deployment_id=did, action="structure").all()
        assert any("取消钉住" in lg.object_label for lg in logs)

    # 已删除 → 404；清单为空
    assert env["client"].delete(
        f"/api/v1/deployments/{did}/qa/pins/{page_id}").status_code == 404
    assert env["client"].get(f"/api/v1/deployments/{did}/qa/pins").get_json()["pins"] == []


def test_export_includes_dashboard_page(app, env):
    did = _deploy(env["client"])
    _pin(env["client"], did)
    export = env["client"].get(f"/api/v1/deployments/{did}/export").get_json()
    assert any(p.get("kind") == "dashboard" for p in export["page_specs"])
