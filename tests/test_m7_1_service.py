"""M7-1 配置服务：读 / 存 / 重置（0.13.0 / 技术方案 M7-1）。

覆盖：保存回读一致、蓝图新增字段默认映射、删除字段残留忽略、损坏降级、
原子回滚、审计落库、候选与权限列拒绝、恢复默认。
"""
from __future__ import annotations

import json

import pytest

from datamason.forms.service import (
    FormConfigError,
    get_form_config,
    reset_field_config,
    save_form_config,
)


def _seed(app) -> int:
    from datamason.models import Deployment, TableSpec

    cols = [
        {"name": "id", "biz_name": "编号", "type": "INTEGER", "is_pk": True,
         "nullable": False},
        {"name": "status", "biz_name": "状态", "type": "VARCHAR", "length": 255},
        {"name": "tags", "biz_name": "标签", "type": "VARCHAR", "length": 255},
        {"name": "due_at", "biz_name": "截止时间", "type": "DATETIME"},
        {"name": "attachment", "biz_name": "附件", "type": "FILE"},
        {"name": "customer_id", "biz_name": "客户", "type": "INTEGER", "is_fk": True,
         "ref_table": "customers", "ref_column": "id"},
        {"name": "created_by", "biz_name": "创建人", "type": "INTEGER",
         "is_ownership": True},
    ]
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        d = Deployment(biz_name="测试部署", owner_id=1)
        s.add(d)
        s.flush()
        s.add(TableSpec(deployment_id=d.id, name="orders", biz_name="订单",
                        columns_json=json.dumps(cols, ensure_ascii=False)))
        s.commit()
        return d.id


def _payload():
    return {
        "fields": [
            {"field_name": "status", "control_type": "radio",
             "options": [{"value": "new", "label": "新建"},
                         {"value": "done", "label": "完成"}]},
            {"field_name": "tags", "control_type": "checkbox",
             "options": [{"value": "a", "label": "A"}, {"value": "b", "label": "B"}]},
            {"field_name": "due_at", "control_type": "date", "time_granularity": "date"},
            {"field_name": "attachment", "control_type": "file"},
            {"field_name": "customer_id", "control_type": "ref"},
        ],
        "layout": {"columns_per_row": 2, "order": [{"field_name": "status"}],
                   "groups": [{"title": "基本信息", "field_names": ["status", "tags"]}]},
    }


def _by_field(cfg):
    return {f["field_name"]: f for f in cfg["fields"]}


def test_save_and_read_back(app):
    did = _seed(app)
    save_form_config(app, did, "orders", _payload(), actor_user_id=7)
    cfg = get_form_config(app, did, "orders")
    fields = _by_field(cfg)
    assert cfg["degraded"] is False
    assert fields["status"]["control_type"] == "radio"
    assert fields["status"]["options"] == [
        {"value": "new", "label": "新建"}, {"value": "done", "label": "完成"}]
    assert fields["tags"]["control_type"] == "checkbox"
    assert fields["due_at"]["control_type"] == "date"
    assert fields["due_at"]["time_granularity"] == "date"
    assert fields["attachment"]["control_type"] == "file"
    assert fields["customer_id"]["control_type"] == "ref"
    assert cfg["layout"]["columns_per_row"] == 2
    assert cfg["layout"]["order"] == ["status"]
    assert cfg["layout"]["groups"][0]["title"] == "基本信息"
    assert cfg["options"]["status"][0]["value"] == "new"


def test_blueprint_new_field_uses_default(app):
    did = _seed(app)
    save_form_config(app, did, "orders", _payload())
    from datamason.models import TableSpec

    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        ts = s.query(TableSpec).filter_by(deployment_id=did, name="orders").first()
        cols = json.loads(ts.columns_json)
        cols.append({"name": "remark", "biz_name": "备注", "type": "TEXT"})
        ts.columns_json = json.dumps(cols, ensure_ascii=False)
        s.commit()
    cfg = get_form_config(app, did, "orders")
    fields = _by_field(cfg)
    assert fields["remark"]["control_type"] == "textarea"
    assert fields["remark"]["configured"] is False
    assert fields["status"]["control_type"] == "radio"


def test_deleted_field_config_ignored(app):
    did = _seed(app)
    from datamason.models import FormFieldConfig

    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        s.add(FormFieldConfig(deployment_id=did, table_name="orders",
                              field_name="ghost", control_type="text"))
        s.commit()
    cfg = get_form_config(app, did, "orders")
    assert "ghost" not in _by_field(cfg)
    assert cfg["degraded"] is False


def test_corrupt_config_degrades(app):
    did = _seed(app)
    from datamason.models import FormFieldConfig

    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        s.add(FormFieldConfig(deployment_id=did, table_name="orders",
                              field_name="status", control_type="unknown_ctrl"))
        s.commit()
    cfg = get_form_config(app, did, "orders")
    assert cfg["degraded"] is True
    assert _by_field(cfg)["status"]["control_type"] == "text"


def test_atomic_rollback_keeps_old(app, monkeypatch):
    did = _seed(app)
    save_form_config(app, did, "orders", _payload())
    import datamason.forms.service as svc

    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(svc, "_field_row", _boom)
    with pytest.raises(FormConfigError):
        save_form_config(app, did, "orders", {
            "fields": [{"field_name": "status", "control_type": "textarea"}],
        })
    cfg = get_form_config(app, did, "orders")
    assert _by_field(cfg)["status"]["control_type"] == "radio"
    assert cfg["layout"]["columns_per_row"] == 2


def test_audit_written(app):
    did = _seed(app)
    save_form_config(app, did, "orders", _payload(), actor_user_id=7)
    from datamason.models import ActivityLog

    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        rows = s.query(ActivityLog).filter_by(
            deployment_id=did, action="form_config").all()
    assert len(rows) == 1
    assert rows[0].status == "committed"
    assert rows[0].actor_user_id == 7
    assert rows[0].object_table == "orders"


def test_reject_duplicate_options(app):
    did = _seed(app)
    with pytest.raises(FormConfigError):
        save_form_config(app, did, "orders", {
            "fields": [{"field_name": "status", "control_type": "radio",
                        "options": [{"value": "a", "label": "A"},
                                    {"value": "a", "label": "B"}]}],
        })


def test_reject_too_many_options(app):
    did = _seed(app)
    options = [{"value": f"v{i}", "label": f"L{i}"} for i in range(201)]
    with pytest.raises(FormConfigError):
        save_form_config(app, did, "orders", {
            "fields": [{"field_name": "status", "control_type": "radio",
                        "options": options}],
        })


def test_reject_pk_and_ownership(app):
    did = _seed(app)
    with pytest.raises(FormConfigError):
        save_form_config(app, did, "orders", {
            "fields": [{"field_name": "id", "control_type": "number"}],
        })
    with pytest.raises(FormConfigError):
        save_form_config(app, did, "orders", {
            "fields": [{"field_name": "created_by", "control_type": "number"}],
        })


def test_reject_unknown_field(app):
    did = _seed(app)
    with pytest.raises(FormConfigError):
        save_form_config(app, did, "orders", {
            "fields": [{"field_name": "nope", "control_type": "text"}],
        })


def test_reject_incompatible_control(app):
    did = _seed(app)
    with pytest.raises(FormConfigError):
        save_form_config(app, did, "orders", {
            "fields": [{"field_name": "due_at", "control_type": "file"}],
        })


def test_reset_field_config(app):
    did = _seed(app)
    save_form_config(app, did, "orders", _payload())
    reset_field_config(app, did, "orders", "status", actor_user_id=7)
    cfg = get_form_config(app, did, "orders")
    fields = _by_field(cfg)
    assert fields["status"]["configured"] is False
    assert fields["status"]["control_type"] == "text"
    assert fields["status"]["options"] == []
    assert fields["tags"]["control_type"] == "checkbox"


def test_unknown_table_raises(app):
    did = _seed(app)
    with pytest.raises(FormConfigError):
        get_form_config(app, did, "nope")