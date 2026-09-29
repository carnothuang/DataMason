"""M9-1 写校验扩展：五类新控件的值校验与 control_config 交叉校验（0.15.0 / 技术方案 §四）。

覆盖：switch 非 0/1 拒绝、slider 越界/错步长/整数列小数拒绝、rate 超星/整数列半星拒绝、
select 值不在候选项拒绝、cascader 父节点/未知值拒绝、合法值通过、未配置零变化。
"""
from __future__ import annotations

import pytest

from datamason.auth.service import hash_password
from datamason.forms import service as form_service
from datamason.models import PageSpec, TableSpec, User

SPEC = {
    "biz_notes": "控件校验用",
    "tables": [
        {
            "name": "items",
            "biz_name": "条目",
            "columns": [
                {"name": "id", "biz_name": "主键", "type": "INTEGER",
                 "is_pk": True, "autoincrement": True, "nullable": False},
                {"name": "name", "biz_name": "名称", "type": "VARCHAR",
                 "length": 64, "nullable": False},
                {"name": "enabled", "biz_name": "启用", "type": "BOOLEAN"},
                {"name": "score", "biz_name": "分数", "type": "INTEGER"},
                {"name": "amount", "biz_name": "金额", "type": "DECIMAL",
                 "precision": 10, "scale": 2},
                {"name": "level", "biz_name": "级别", "type": "VARCHAR", "length": 32},
                {"name": "region", "biz_name": "地区", "type": "VARCHAR", "length": 32},
            ],
            "indexes": [],
        },
    ],
}


class FakeLlm:
    def chat_json(self, system_prompt, user_prompt):
        class _Usage:
            tokens_in = 1
            tokens_out = 1
            duration_ms = 1

        return SPEC, _Usage()


@pytest.fixture()
def admin(app, client):
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        u = User(username="laomao", display_name="老猫",
                 password_hash=hash_password("pw"), is_platform_admin=True)
        s.add(u)
        s.commit()
        uid = u.id
    assert client.post("/api/v1/auth/login",
                       json={"username": "laomao", "password": "pw"}).status_code == 200
    return uid


@pytest.fixture()
def patch_llm(monkeypatch):
    monkeypatch.setattr(
        "datamason.settings.service.get_default_llm_client",
        lambda _app: FakeLlm(),
    )
    yield


def _deploy(client) -> int:
    preview = client.post("/api/v1/deployments/preview",
                          json={"description": "测试"}).get_json()
    result = client.post("/api/v1/deployments/confirm", json={
        "spec_json": preview["spec_json"],
        "biz_name": "校验验证",
        "seed_data": False,
    }).get_json()
    return result["deployment_id"]


def _config() -> dict:
    return {
        "fields": [
            {"field_name": "enabled", "control_type": "switch"},
            {"field_name": "score", "control_type": "slider",
             "control_config": {"min": 0, "max": 100, "step": 10}},
            {"field_name": "amount", "control_type": "rate",
             "control_config": {"max_stars": 5, "allow_half": True}},
            {"field_name": "level", "control_type": "select",
             "options": [{"value": "a", "label": "A"},
                         {"value": "b", "label": "B"}]},
            {"field_name": "region", "control_type": "cascader",
             "options": [
                 {"value": "zj", "label": "浙江"},
                 {"value": "hz", "label": "杭州", "parent_value": "zj"},
                 {"value": "xh", "label": "西湖", "parent_value": "hz"},
             ]},
        ],
    }


# ---------- switch ----------

def test_switch_valid(app, client, admin, patch_llm):
    did = _deploy(client)
    form_service.save_form_config(app, did, "items", _config(),
                                  page_kind="workspace", actor_user_id=admin)
    base = f"/api/v1/app/{did}/api/tables/items/rows"
    ok = client.post(base, json={"name": "i", "enabled": 1})
    assert ok.status_code == 201, ok.get_data(as_text=True)
    row = client.get(f"{base}/{ok.get_json()['id']}").get_json()
    assert row["enabled"] == 1  # BOOLEAN 列落库 1


def test_switch_rejects_non_bool(app, client, admin, patch_llm):
    did = _deploy(client)
    form_service.save_form_config(app, did, "items", _config(),
                                  page_kind="workspace", actor_user_id=admin)
    base = f"/api/v1/app/{did}/api/tables/items/rows"
    # BOOLEAN 列 coerce 后为 int：合法值 1/0，其他整数（如 2）应被校验拒绝
    bad = client.post(base, json={"name": "i", "enabled": 2})
    assert bad.status_code == 400
    assert "1 或 0" in bad.get_json()["error"]


# ---------- slider ----------

def test_slider_valid_and_floor(app, client, admin, patch_llm):
    did = _deploy(client)
    form_service.save_form_config(app, did, "items", _config(),
                                  page_kind="workspace", actor_user_id=admin)
    base = f"/api/v1/app/{did}/api/tables/items/rows"
    ok = client.post(base, json={"name": "i", "score": 50})
    assert ok.status_code == 201, ok.get_data(as_text=True)


def test_slider_rejects_out_of_range(app, client, admin, patch_llm):
    did = _deploy(client)
    form_service.save_form_config(app, did, "items", _config(),
                                  page_kind="workspace", actor_user_id=admin)
    base = f"/api/v1/app/{did}/api/tables/items/rows"
    bad = client.post(base, json={"name": "i", "score": 101})
    assert bad.status_code == 400
    assert "100" in bad.get_json()["error"]


def test_slider_rejects_wrong_step(app, client, admin, patch_llm):
    did = _deploy(client)
    form_service.save_form_config(app, did, "items", _config(),
                                  page_kind="workspace", actor_user_id=admin)
    base = f"/api/v1/app/{did}/api/tables/items/rows"
    bad = client.post(base, json={"name": "i", "score": 55})
    assert bad.status_code == 400
    assert "整数倍" in bad.get_json()["error"]


def test_slider_rejects_decimal_on_integer_col(app, client, admin, patch_llm):
    did = _deploy(client)
    form_service.save_form_config(app, did, "items", _config(),
                                  page_kind="workspace", actor_user_id=admin)
    base = f"/api/v1/app/{did}/api/tables/items/rows"
    bad = client.post(base, json={"name": "i", "score": 5.5})
    assert bad.status_code == 400


# ---------- rate ----------

def test_rate_valid_half(app, client, admin, patch_llm):
    did = _deploy(client)
    form_service.save_form_config(app, did, "items", _config(),
                                  page_kind="workspace", actor_user_id=admin)
    base = f"/api/v1/app/{did}/api/tables/items/rows"
    ok = client.post(base, json={"name": "i", "amount": 3.5})
    assert ok.status_code == 201, ok.get_data(as_text=True)


def test_rate_rejects_over_stars(app, client, admin, patch_llm):
    did = _deploy(client)
    form_service.save_form_config(app, did, "items", _config(),
                                  page_kind="workspace", actor_user_id=admin)
    base = f"/api/v1/app/{did}/api/tables/items/rows"
    bad = client.post(base, json={"name": "i", "amount": 6})
    assert bad.status_code == 400
    assert "1~5" in bad.get_json()["error"]


def test_rate_rejects_non_half(app, client, admin, patch_llm):
    did = _deploy(client)
    form_service.save_form_config(app, did, "items", _config(),
                                  page_kind="workspace", actor_user_id=admin)
    base = f"/api/v1/app/{did}/api/tables/items/rows"
    bad = client.post(base, json={"name": "i", "amount": 3.3})
    assert bad.status_code == 400
    assert "半星" in bad.get_json()["error"]


# ---------- select ----------

def test_select_valid(app, client, admin, patch_llm):
    did = _deploy(client)
    form_service.save_form_config(app, did, "items", _config(),
                                  page_kind="workspace", actor_user_id=admin)
    base = f"/api/v1/app/{did}/api/tables/items/rows"
    ok = client.post(base, json={"name": "i", "level": "a"})
    assert ok.status_code == 201, ok.get_data(as_text=True)


def test_select_rejects_unknown_value(app, client, admin, patch_llm):
    did = _deploy(client)
    form_service.save_form_config(app, did, "items", _config(),
                                  page_kind="workspace", actor_user_id=admin)
    base = f"/api/v1/app/{did}/api/tables/items/rows"
    bad = client.post(base, json={"name": "i", "level": "zzz"})
    assert bad.status_code == 400
    assert "候选项" in bad.get_json()["error"]


# ---------- cascader ----------

def test_cascader_valid_leaf(app, client, admin, patch_llm):
    did = _deploy(client)
    form_service.save_form_config(app, did, "items", _config(),
                                  page_kind="workspace", actor_user_id=admin)
    base = f"/api/v1/app/{did}/api/tables/items/rows"
    ok = client.post(base, json={"name": "i", "region": "xh"})
    assert ok.status_code == 201, ok.get_data(as_text=True)


def test_cascader_rejects_parent_value(app, client, admin, patch_llm):
    did = _deploy(client)
    form_service.save_form_config(app, did, "items", _config(),
                                  page_kind="workspace", actor_user_id=admin)
    base = f"/api/v1/app/{did}/api/tables/items/rows"
    # 父节点（有子项的 zj / hz）不可作为落库值
    bad = client.post(base, json={"name": "i", "region": "hz"})
    assert bad.status_code == 400
    assert "末级" in bad.get_json()["error"]


def test_cascader_rejects_unknown(app, client, admin, patch_llm):
    did = _deploy(client)
    form_service.save_form_config(app, did, "items", _config(),
                                  page_kind="workspace", actor_user_id=admin)
    base = f"/api/v1/app/{did}/api/tables/items/rows"
    bad = client.post(base, json={"name": "i", "region": "nope"})
    assert bad.status_code == 400


# ---------- control_config 交叉校验（保存时拒绝） ----------

def test_save_rejects_half_star_on_integer_col(app, client, admin, patch_llm):
    did = _deploy(client)
    from datamason.forms.service import FormConfigError

    with pytest.raises(FormConfigError):
        form_service.save_form_config(app, did, "items", {
            "fields": [{"field_name": "score", "control_type": "rate",
                        "control_config": {"max_stars": 5, "allow_half": True}}],
        }, page_kind="workspace", actor_user_id=admin)


def test_save_rejects_decimal_step_on_integer_col(app, client, admin, patch_llm):
    did = _deploy(client)
    from datamason.forms.service import FormConfigError

    with pytest.raises(FormConfigError):
        form_service.save_form_config(app, did, "items", {
            "fields": [{"field_name": "score", "control_type": "slider",
                        "control_config": {"min": 0, "max": 1, "step": 0.1}}],
        }, page_kind="workspace", actor_user_id=admin)


# ---------- 未配置零变化 ----------

def test_unconfigured_unchanged(app, client, admin, patch_llm):
    did = _deploy(client)
    base = f"/api/v1/app/{did}/api/tables/items/rows"
    # 未配置控件：VARCHAR 列任意值放行（与升级前一致；INTEGER 列会被 coerce 拒绝，非本版改动）
    ok = client.post(base, json={"name": "i", "level": "任意值"})
    assert ok.status_code == 201
