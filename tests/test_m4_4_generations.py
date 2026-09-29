"""M4-4 验收：AI 生成历史治理（ai_generations 埋点 + 治理页接口）。

- 生成点接线：部署提案（deploy）、种子（seed）、问数据（qa）各留一条历史；
  ai_generations 增 deployment_id（惰性 ALTER 回填）。
- 治理接口（平台管理员）：列表筛选（kind/deployment_id/status）+ 统计（token/耗时/按 kind）+
  清理（必须给条件，避免误清全表）/ 单条删除。
- 工作台工程室：本部署生成历史（创建人/管理员 200，操作员 403）。
- 清理只删历史，不影响业务数据与页面。
"""
from __future__ import annotations

import pytest

from datamason.auth.service import hash_password
from datamason.llm.client import Usage
from datamason.models import AiGeneration, DeploymentMember, User

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


class FakeLlm:
    def chat_json(self, system_prompt, user_prompt):
        if "只读 SQL" in system_prompt:
            return ({"sql": "SELECT name, amount FROM messages", "title": "留言"},
                    Usage(tokens_in=7, tokens_out=5, duration_ms=3))
        if "总结" in system_prompt:
            return ({"conclusion": "共 10 条。"},
                    Usage(tokens_in=3, tokens_out=2, duration_ms=1))
        return SPEC, Usage(tokens_in=11, tokens_out=13, duration_ms=9)


@pytest.fixture()
def fake_llm(monkeypatch):
    class _C:
        def chat_json(self, sp, up):
            return FakeLlm().chat_json(sp, up)

    monkeypatch.setattr(
        "datamason.settings.service.get_default_llm_client", lambda _app: _C())
    yield


@pytest.fixture()
def env(app):
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        s.add(User(username="laomao", display_name="老猫",
                   password_hash=hash_password("pw"), is_platform_admin=True))
        s.add(User(username="plain", display_name="普通用户",
                   password_hash=hash_password("pw"), is_platform_admin=False))
        s.commit()
    c = app.test_client()
    assert c.post("/api/v1/auth/login",
                  json={"username": "laomao", "password": "pw"}).status_code == 200
    return {"client": c}


def _session(app):
    return app.extensions["datamason_session_factory"]()


def _login(app, username: str):
    c = app.test_client()
    assert c.post("/api/v1/auth/login",
                  json={"username": username, "password": "pw"}).status_code == 200
    return c


def _deploy(client) -> int:
    resp = client.post("/api/v1/deployments/confirm", json={
        "spec_json": SPEC, "biz_name": "生成历史演示", "seed_data": True,
    })
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()["deployment_id"]


def test_generation_points_are_recorded(app, env, fake_llm):
    client = env["client"]
    # 部署提案（NL→结构）
    assert client.post("/api/v1/deployments/preview",
                       json={"description": "留言板"}).status_code == 200
    did = _deploy(client)

    with _session(app) as s:
        rows = s.query(AiGeneration).order_by(AiGeneration.id).all()
        kinds = [r.kind for r in rows]
        assert "deploy" in kinds and "seed" in kinds
        deploy = next(r for r in rows if r.kind == "deploy")
        assert deploy.deployment_id == 0 and deploy.tokens_in == 11
        seed = next(r for r in rows if r.kind == "seed")
        assert seed.deployment_id == did and seed.status == "ok"
        # 列已回填（惰性 ALTER）
    # 问数据（qa）带 deployment_id
    assert client.post(f"/api/v1/app/{did}/qa/ask",
                       json={"question": "有多少留言"}).status_code == 200
    with _session(app) as s:
        qa = (
            s.query(AiGeneration).filter_by(kind="qa")
            .order_by(AiGeneration.id.desc()).first()
        )
        assert qa is not None and qa.deployment_id == did and qa.tokens_in == 10


def test_settings_list_filters_and_stats(app, env, fake_llm):
    client = env["client"]
    assert client.post("/api/v1/deployments/preview",
                       json={"description": "留言板"}).status_code == 200
    did = _deploy(client)

    body = client.get("/api/v1/settings/ai-generations").get_json()
    assert body["total"] >= 2 and body["stats"]["total"] == body["total"]
    assert body["stats"]["by_kind"].get("seed") == 1
    # 时间倒序
    ids = [it["id"] for it in body["items"]]
    assert ids == sorted(ids, reverse=True)

    only_seed = client.get(
        "/api/v1/settings/ai-generations?kind=seed").get_json()
    assert only_seed["total"] == 1 and only_seed["items"][0]["kind"] == "seed"
    assert only_seed["items"][0]["deployment_id"] == did
    assert only_seed["items"][0]["deployment_name"] == "生成历史演示"

    by_dep = client.get(
        f"/api/v1/settings/ai-generations?deployment_id={did}").get_json()
    assert all(it["deployment_id"] == did for it in by_dep["items"])
    assert all(it["deployment_id"] != 0 for it in by_dep["items"])


def test_settings_require_platform_admin(app, env):
    plain = _login(app, "plain")
    assert plain.get("/api/v1/settings/ai-generations").status_code == 403


def test_cleanup_and_delete(app, env, fake_llm):
    client = env["client"]
    # 失败记录（LLM 未配置的直连补标签会写 error；这里直接手造两条便于断言）
    with _session(app) as s:
        s.add(AiGeneration(kind="qa", status="error", prompt_summary="坏问题",
                           result_summary="只读门", deployment_id=0))
        s.add(AiGeneration(kind="qa", status="ok", prompt_summary="好问题",
                           result_summary="ok", deployment_id=0))
        s.commit()

    # 无条件下清理 → 400（防误清）
    assert client.post("/api/v1/settings/ai-generations/cleanup",
                       json={}).status_code == 400

    resp = client.post("/api/v1/settings/ai-generations/cleanup",
                       json={"status": "error"})
    assert resp.status_code == 200 and resp.get_json()["deleted"] >= 1
    with _session(app) as s:
        assert s.query(AiGeneration).filter_by(status="error").count() == 0

    # 单条删除
    with _session(app) as s:
        gid = s.query(AiGeneration).first().id
    assert client.delete(f"/api/v1/settings/ai-generations/{gid}").status_code == 200
    assert client.delete(f"/api/v1/settings/ai-generations/{gid}").status_code == 404


def test_cleanup_does_not_touch_business_data(app, env, fake_llm):
    client = env["client"]
    did = _deploy(client)
    before = client.get(
        f"/api/v1/app/{did}/api/tables/messages/rows?page_size=1").get_json()["total"]
    client.post("/api/v1/settings/ai-generations/cleanup", json={"deployment_id": did})
    after = client.get(
        f"/api/v1/app/{did}/api/tables/messages/rows?page_size=1").get_json()["total"]
    assert after == before == 10


def test_workspace_generations_role_scope(app, env, fake_llm):
    did = _deploy(env["client"])
    with _session(app) as s:
        op_id = User(username="op", display_name="操作员",
                     password_hash=hash_password("pw"))
        s.add(op_id)
        s.commit()
        s.add(DeploymentMember(deployment_id=did, user_id=op_id.id, role="operator"))
        s.commit()

    creator = env["client"].get(f"/api/v1/deployments/{did}/ai-generations")
    assert creator.status_code == 200
    assert creator.get_json()["total"] >= 1

    op = _login(app, "op")
    assert op.get(f"/api/v1/deployments/{did}/ai-generations").status_code == 403
