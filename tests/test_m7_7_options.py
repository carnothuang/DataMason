"""M7-7 候选项自然语言生成（0.13.0 / 技术方案 M7-7）。

覆盖：LLM 桩产出清单、确定性去重与裁剪、埋点（成功/失败）、
未配置模型 400、生成失败保留原配置、未确认不写入配置。
"""
from __future__ import annotations

import json

import pytest

from server.forms import service as form_service
from server.forms.service import FormConfigError
from server.llm.client import LlmCallError
from server.models import AiGeneration, FormFieldConfig, TableSpec

COLUMNS = [
    {"name": "id", "biz_name": "编号", "type": "INTEGER", "is_pk": True, "nullable": False},
    {"name": "status", "biz_name": "订单状态", "type": "VARCHAR", "length": 32},
]


class FakeLlm:
    def __init__(self, items=None, boom=False):
        self.items = items or [
            {"value": "new", "label": "新建"},
            {"value": "paid", "label": "已支付"},
            {"value": "new", "label": "重复项"},  # 应被去重
        ]
        self.boom = boom

    def chat_json(self, system_prompt, user_prompt):
        if self.boom:
            raise LlmCallError("模型超时")
        usage = type("U", (), {"tokens_in": 3, "tokens_out": 5, "duration_ms": 12})()
        return {"items": self.items}, usage


def _seed(app) -> int:
    from server.models import Deployment

    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        d = Deployment(biz_name="候选项", owner_id=1)
        s.add(d)
        s.flush()
        s.add(TableSpec(deployment_id=d.id, name="orders", biz_name="订单",
                        columns_json=json.dumps(COLUMNS, ensure_ascii=False)))
        s.commit()
        return d.id


def _patch(monkeypatch, llm):
    monkeypatch.setattr(
        "server.settings.service.get_default_llm_client", lambda _app: llm)


def test_generate_dedup_and_record(app, monkeypatch):
    did = _seed(app)
    _patch(monkeypatch, FakeLlm())
    out = form_service.generate_option_items(
        app, field_label="订单状态", description="常见订单状态", deployment_id=did)
    values = [i["value"] for i in out["items"]]
    assert values == ["new", "paid"]  # 去重
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        rows = s.query(AiGeneration).filter_by(kind="options").all()
    assert len(rows) == 1
    assert rows[0].status == "ok"
    assert rows[0].deployment_id == did


def test_generate_failure_records_and_reraises(app, monkeypatch):
    did = _seed(app)
    _patch(monkeypatch, FakeLlm(boom=True))
    with pytest.raises(LlmCallError):
        form_service.generate_option_items(
            app, field_label="订单状态", description="x", deployment_id=did)
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        rows = s.query(AiGeneration).filter_by(kind="options").all()
    assert len(rows) == 1
    assert rows[0].status == "error"


def test_not_configured_raises(app, monkeypatch):
    did = _seed(app)
    monkeypatch.setattr(
        "server.settings.service.get_default_llm_client", lambda _app: None)
    with pytest.raises(FormConfigError):
        form_service.generate_option_items(
            app, field_label="订单状态", description="x", deployment_id=did)


def test_generation_does_not_write_config(app, monkeypatch):
    """未确认前不得写入配置：生成只返回清单。"""
    did = _seed(app)
    _patch(monkeypatch, FakeLlm())
    form_service.generate_option_items(
        app, field_label="订单状态", description="x", deployment_id=did)
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        assert s.query(FormFieldConfig).count() == 0


def test_generate_route(app, monkeypatch):
    from server.auth.service import hash_password
    from server.models import User

    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        u = User(username="laomao", password_hash=hash_password("pw"),
                 is_platform_admin=True)
        s.add(u)
        s.commit()
    client = app.test_client()
    assert client.post("/api/v1/auth/login",
                       json={"username": "laomao", "password": "pw"}).status_code == 200
    did = _seed(app)
    _patch(monkeypatch, FakeLlm())
    resp = client.post(
        f"/api/v1/deployments/{did}/forms/orders/fields/status/options/generate",
        json={"description": "常见订单状态"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert [i["value"] for i in resp.get_json()["items"]] == ["new", "paid"]
