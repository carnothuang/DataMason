"""M9-8 运行态渲染公开页安全子集（0.15.0 / 开发计划 §M9-8-4）。

公开页仅消费表级 UI 配置的安全子集——列显隐 / 列序：
- `list.visible_columns` 非空：清单外字段在公开表单隐藏，清单顺序作为公开页 order；
- 未配置 / `"{}"` / 损坏：公开页行为零变化（回归 0.14）；
- 公开页不开放区块画布 / 拖拽 / 详情等任何写配置入口；
- 访客提交仍走 CRUD 写路径（隐藏字段不会因前端裁剪而被绕过写库）。
"""
from __future__ import annotations

import pytest

from datamason.auth.service import hash_password
from datamason.models import User

GUESTBOOK_SPEC = {
    "biz_notes": "访客登记",
    "tables": [
        {
            "name": "guests",
            "biz_name": "留言",
            "columns": [
                {"name": "id", "biz_name": "主键", "type": "INTEGER",
                 "is_pk": True, "autoincrement": True, "nullable": False},
                {"name": "name", "biz_name": "姓名", "type": "VARCHAR",
                 "length": 64, "nullable": False},
                {"name": "phone", "biz_name": "电话", "type": "VARCHAR",
                 "length": 32},
                {"name": "note", "biz_name": "备注", "type": "TEXT"},
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
        "datamason.settings.service.get_default_llm_client",
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
    c = app.test_client()
    assert c.post("/api/v1/auth/login",
                  json={"username": "laomao", "password": "pw"}).status_code == 200
    return c


def _deploy(client) -> int:
    preview = client.post("/api/v1/deployments/preview",
                          json={"description": "登记台"}).get_json()
    result = client.post("/api/v1/deployments/confirm", json={
        "spec_json": preview["spec_json"],
        "biz_name": "登记台",
        "seed_data": False,
    }).get_json()
    return result["deployment_id"]


def _public_page(client, did: int) -> dict:
    pages = client.get(f"/api/v1/deployments/{did}/interfaces").get_json()["pages"]
    return next(p for p in pages if p["kind"] == "public_form")


def _public_meta(app, did: int, page_id: int) -> dict:
    return app.test_client().get(
        f"/api/v1/public/{did}/pages/{page_id}").get_json()


def test_public_subset_hides_and_orders(app, env, patch_llm):
    did = _deploy(env)
    page = _public_page(env, did)

    payload = {
        "version": 1,
        "list": {"visible_columns": ["phone", "name"]},
    }
    r = env.put(f"/api/v1/deployments/{did}/tables/guests/ui-config", json=payload)
    assert r.status_code == 200, r.get_data(as_text=True)

    meta = _public_meta(app, did, page["id"])
    by = {f["name"]: f for f in meta["fields"]}
    assert by["note"].get("hidden") is True
    assert by["name"].get("hidden") is not True
    assert by["phone"].get("hidden") is not True
    assert meta["layout"]["order"] == ["phone", "name"]


def test_public_subset_zero_change_when_unconfigured(app, env, patch_llm):
    did = _deploy(env)
    page = _public_page(env, did)
    meta = _public_meta(app, did, page["id"])
    assert "layout" not in meta
    assert all(not f.get("hidden") for f in meta["fields"])


def test_public_subset_zero_change_on_empty_config(app, env, patch_llm):
    did = _deploy(env)
    page = _public_page(env, did)
    assert env.put(f"/api/v1/deployments/{did}/tables/guests/ui-config",
                   json={"version": 1, "list": {}}).status_code == 200
    meta = _public_meta(app, did, page["id"])
    assert all(not f.get("hidden") for f in meta["fields"])


def test_public_subset_still_submits(app, env, patch_llm):
    did = _deploy(env)
    page = _public_page(env, did)
    env.put(f"/api/v1/deployments/{did}/tables/guests/ui-config",
            json={"version": 1, "list": {"visible_columns": ["name"]}})

    anon = app.test_client()
    ok = anon.post(f"/api/v1/public/{did}/pages/{page['id']}/submit",
                   json={"name": "访客甲", "note": "不应阻断"})
    assert ok.status_code == 201, ok.get_data(as_text=True)

    rows = env.get(
        f"/api/v1/app/{did}/api/tables/guests/rows").get_json()["data"]
    row = next(r for r in rows if r["name"] == "访客甲")
    assert row["note"] == "不应阻断"
