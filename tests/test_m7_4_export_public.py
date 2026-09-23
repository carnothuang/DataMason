"""M7-4 导出扩展与公开页元数据扩展（0.13.0 / 技术方案 M7-4）。

覆盖：导出三节 + counts、无配置表不出现在导出；公开页仅下发本页字段与候选项、
公开配置缺省时响应与升级前一致。
"""
from __future__ import annotations

import json

from server.deploy import export as export_service
from server.forms import service as form_service
from server.models import PageSpec, TableSpec
from server.public import service as public_service

COLUMNS = [
    {"name": "id", "biz_name": "编号", "type": "INTEGER", "is_pk": True, "nullable": False},
    {"name": "status", "biz_name": "状态", "type": "VARCHAR", "length": 32},
    {"name": "created_by", "biz_name": "创建人", "type": "INTEGER", "is_ownership": True},
]


def _seed(app, running=True) -> tuple[int, int, int]:
    from server.models import Deployment

    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        d = Deployment(biz_name="导出验证", owner_id=1,
                       status="running" if running else "draft")
        s.add(d)
        s.flush()
        ts = TableSpec(deployment_id=d.id, name="orders", biz_name="订单",
                       columns_json=json.dumps(COLUMNS, ensure_ascii=False))
        s.add(ts)
        s.flush()
        page = PageSpec(deployment_id=d.id, kind="public_form", access="public",
                        table_id=ts.id, title="订单登记")
        s.add(page)
        s.commit()
        return d.id, ts.id, page.id


def _payload():
    return {
        "fields": [{"field_name": "status", "control_type": "radio",
                    "options": [{"value": "new", "label": "新建"},
                                {"value": "done", "label": "完成"}]}],
        "layout": {"columns_per_row": 2, "order": [{"field_name": "status"}],
                   "groups": [{"title": "基本信息", "field_names": ["status"]}]},
    }


def test_export_includes_form_sections(app):
    did, _ts, _page = _seed(app)
    form_service.save_form_config(app, did, "orders", _payload(),
                                  page_kind="workspace", actor_user_id=1)
    pkg = export_service.build_package(app, did)
    assert pkg["form_field_configs"][0]["field_name"] == "status"
    assert pkg["form_field_configs"][0]["control_type"] == "radio"
    assert pkg["form_option_items"][0]["value"] == "new"
    assert pkg["form_layouts"][0]["page_kind"] == "workspace"
    assert pkg["form_layouts"][0]["columns_per_row"] == 2
    assert pkg["counts"]["form_field_configs"] == 1
    assert pkg["counts"]["form_option_items"] == 2
    assert pkg["counts"]["form_layouts"] == 1
    assert pkg["version"] == 1


def test_export_omits_unconfigured_tables(app):
    did, _ts, _page = _seed(app)
    pkg = export_service.build_package(app, did)
    assert pkg["form_field_configs"] == []
    assert pkg["form_option_items"] == []
    assert pkg["form_layouts"] == []


def test_public_page_includes_public_config_only(app):
    did, _ts, page_id = _seed(app)
    form_service.save_form_config(app, did, "orders", _payload(),
                                  page_kind="public", actor_user_id=1)
    meta = public_service.get_page(app, did, page_id)
    fields = {f["name"]: f for f in meta["fields"]}
    assert "created_by" not in fields  # 归属列不下发
    assert fields["status"]["control_type"] == "radio"
    assert fields["status"]["options"][0]["value"] == "new"
    assert meta["layout"]["columns_per_row"] == 2
    assert meta["layout"]["order"] == ["status"]
    # 最小暴露：不含主键/归属列/他表信息
    assert meta["table"] == "orders"
    assert all("is_ownership" not in f or not f["is_ownership"] for f in meta["fields"])


def test_public_page_default_when_unconfigured(app):
    did, _ts, page_id = _seed(app)
    meta = public_service.get_page(app, did, page_id)
    # 缺省：不含 layout / control_type 等新增可选字段（与升级前一致）
    assert "layout" not in meta
    assert all("control_type" not in f for f in meta["fields"])


def test_public_layout_scoped_by_page_kind(app):
    """布局按页面形态分别存储：仅配了工作台布局时，公开页不下发 layout。"""
    did, _ts, page_id = _seed(app)
    form_service.save_form_config(app, did, "orders", _payload(),
                                  page_kind="workspace", actor_user_id=1)
    meta = public_service.get_page(app, did, page_id)
    assert "layout" not in meta
    # 字段控件/候选项为表级共享配置（form_field_configs 无 page_kind），故公开页可见
    fields = {f["name"]: f for f in meta["fields"]}
    assert fields["status"]["control_type"] == "radio"
