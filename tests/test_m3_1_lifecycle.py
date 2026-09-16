"""M3-1 验收：生命周期 v1（停止 / 再启动）。

- POST /deployments/{id}/stop|start：running↔stopped，创建人独有（DR-14），管理员/操作员 403
- 停止态入口打开返回 status=stopped 提示页；工作台 CRUD 与日志一律 409（数据保留可再启动）
- 再启动后原样恢复（入口可进、CRUD 可写）
- 每次 stop/start 必有 committed 系统动作日志（action=stop/start）
- 非法迁移：停下的再停 400、运行中的再启动 400；停止对未知部署 404
- 清单带 role（前端按创建人裁剪停止/再启动按钮）
"""
from __future__ import annotations

import pytest

from server.auth.service import hash_password
from server.models import ActivityLog, Deployment, DeploymentMember, User

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
    """创建平台管理员并登录（共享 client 与会话用户），返回 env={client, id}。"""
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


def _logs(app, did: int, action: str) -> list[dict]:
    with _session(app) as s:
        rows = (
            s.query(ActivityLog)
            .filter(ActivityLog.deployment_id == did,
                    ActivityLog.action == action,
                    ActivityLog.status == "committed")
            .all()
        )
        return [{"actor_user_id": r.actor_user_id, "action": r.action,
                 "object_label": r.object_label} for r in rows]


def test_stop_then_start_lifecycle(app, env, patch_llm):
    did = _deploy(env["client"])

    # 初始 running
    assert env["client"].get(f"/api/v1/app/{did}").get_json()["status"] == "running"

    # 停止
    resp = env["client"].post(f"/api/v1/deployments/{did}/stop")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["status"] == "stopped"
    with _session(app) as s:
        assert s.get(Deployment, did).status == "stopped"

    # 入口返回停止态提示页（数据保留，可在清单中查看/再启动）
    body = env["client"].get(f"/api/v1/app/{did}").get_json()
    assert body["status"] == "stopped"
    assert body["biz_name"] == "留言板"
    assert body["menus"] == [] and body["pages"] == []

    # 工作台 CRUD 与日志均 409（入口不可操作）
    api = f"/api/v1/app/{did}/api"
    assert env["client"].get(f"{api}/tables/messages/rows").status_code == 409
    assert env["client"].post(f"{api}/tables/messages/rows",
                              json={"visitor": "张三", "content": "停?"}).status_code == 409
    assert env["client"].get(f"{api}/activity-logs").status_code == 409
    # 控制台数据浏览同样门控（与工作台一致）
    assert env["client"].get(f"/api/v1/deployments/{did}/tables").status_code == 409
    assert env["client"].get(
        f"/api/v1/deployments/{did}/tables/messages/rows").status_code == 409

    # 再启动
    resp = env["client"].post(f"/api/v1/deployments/{did}/start")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["status"] == "running"
    with _session(app) as s:
        assert s.get(Deployment, did).status == "running"

    # 恢复：入口 normal + CRUD 可写
    assert env["client"].get(f"/api/v1/app/{did}").get_json()["status"] == "running"
    created = env["client"].post(f"/api/v1/app/{did}/api/tables/messages/rows",
                                 json={"visitor": "李四", "content": "恢复啦"})
    assert created.status_code == 201, created.get_data(as_text=True)
    assert env["client"].get(f"/api/v1/app/{did}/api/tables/messages/rows").status_code == 200


def test_stop_start_audit_logs(app, env, patch_llm):
    did = _deploy(env["client"])
    who = env["id"]
    env["client"].post(f"/api/v1/deployments/{did}/stop")
    env["client"].post(f"/api/v1/deployments/{did}/start")

    stops = _logs(app, did, "stop")
    starts = _logs(app, did, "start")
    assert len(stops) == 1 and stops[0]["actor_user_id"] == who
    assert len(starts) == 1 and starts[0]["actor_user_id"] == who
    assert "已停止" in stops[0]["object_label"]
    assert "已再启动" in starts[0]["object_label"]


def test_stop_creator_only(app, env, patch_llm):
    did = _deploy(env["client"])
    adm_id = _mk_user(app, "adm")
    op_id = _mk_user(app, "op")

    with _session(app) as s:
        s.add(DeploymentMember(deployment_id=did, user_id=adm_id, role="admin"))
        s.add(DeploymentMember(deployment_id=did, user_id=op_id, role="operator"))
        s.commit()

    adm = _login(app, "adm")
    op = _login(app, "op")

    # 管理员/操作员：停止与再启动均 403（DR-14：停止/再启动=创建人独有）
    assert adm.post(f"/api/v1/deployments/{did}/stop").status_code == 403
    assert op.post(f"/api/v1/deployments/{did}/stop").status_code == 403

    # 创建人停止后，管理员/操作员再启动仍 403
    assert env["client"].post(f"/api/v1/deployments/{did}/stop").status_code == 200
    assert adm.post(f"/api/v1/deployments/{did}/start").status_code == 403
    assert op.post(f"/api/v1/deployments/{did}/start").status_code == 403
    # 创建人再启动（恢复现场）
    assert env["client"].post(f"/api/v1/deployments/{did}/start").status_code == 200


def test_invalid_transitions(app, env, patch_llm):
    did = _deploy(env["client"])
    # 运行中的再启动 400
    assert env["client"].post(f"/api/v1/deployments/{did}/start").status_code == 400
    # 停下后：再停 400、再启动 200
    assert env["client"].post(f"/api/v1/deployments/{did}/stop").status_code == 200
    assert env["client"].post(f"/api/v1/deployments/{did}/stop").status_code == 400
    assert env["client"].post(f"/api/v1/deployments/{did}/start").status_code == 200
    # 运行中的再启动 400
    assert env["client"].post(f"/api/v1/deployments/{did}/start").status_code == 400
    # 未知部署 404
    assert env["client"].post("/api/v1/deployments/999/stop").status_code == 404
    assert env["client"].post("/api/v1/deployments/999/start").status_code == 404


def test_frontend_logs_action_meta_covers_system_actions():
    """前端日志动作元数据须覆盖后端全部系统动作（防新增动作漏标签/漏筛选）。

    回归 M3-1：stop/start 曾只写后端、前端 ACTION_META 缺项 → 日志显示英文且不可筛选。
    """
    from pathlib import Path

    from server.audit.service import SYSTEM_ACTIONS

    src = (
        Path(__file__).resolve().parent.parent
        / "web" / "src" / "pages" / "workspace" / "LogsPage.tsx"
    ).read_text(encoding="utf-8")
    meta = src.split("export const ACTION_META", 1)[1].split("};", 1)[0]
    missing = [a for a in SYSTEM_ACTIONS if f"{a}:" not in meta]
    assert not missing, f"LogsPage ACTION_META 缺少系统动作: {missing}"


def test_list_deployments_carries_role(app, env, patch_llm):
    did = _deploy(env["client"])
    adm_id = _mk_user(app, "adminer")
    with _session(app) as s:
        s.add(DeploymentMember(deployment_id=did, user_id=adm_id, role="admin"))
        s.commit()

    mine = env["client"].get("/api/v1/deployments").get_json()
    row = next((d for d in mine if d["id"] == did), None)
    assert row is not None
    assert row["role"] == "creator"

    adminer = _login(app, "adminer")
    theirs = adminer.get("/api/v1/deployments").get_json()
    row2 = next((d for d in theirs if d["id"] == did), None)
    assert row2 is not None
    assert row2["role"] == "admin"