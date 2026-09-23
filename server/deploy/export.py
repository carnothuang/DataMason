"""配置包导出（M3-6，DR-03/§五）：把一套部署的配置与全量 committed 日志导出为可离线打开的 JSON。

包含：部署元信息 + 连接元信息（脱敏，不含 conn_uri/凭证）+ table_specs + page_specs +
deployment_menus + crud_configs + flow_specs + activity_logs（全量 committed，两阶段 pending 不收）。

权限：创建人/管理员可导出（管理员=数据管理职权，DR-14）；操作员/非成员不可。
停止态也可导出（导出是只读动作，不依赖运行态）。
"""
from __future__ import annotations

import json
from datetime import datetime

from server.models import (
    ActivityLog,
    Connection,
    CrudConfig,
    Deployment,
    DeploymentMenu,
    FlowSpec,
    FormFieldConfig,
    FormLayout,
    FormOptionItem,
    PageSpec,
    TableSpec,
    User,
)

EXPORT_FORMAT = "datamason.deployment.export"
EXPORT_VERSION = 1


def _jloads(text: str, default):
    try:
        return json.loads(text) if text else default
    except (ValueError, TypeError):
        return default


def _session(app):
    return app.extensions["datamason_session_factory"]()


def build_package(app, deployment_id: int) -> dict:
    with _session(app) as s:
        d = s.get(Deployment, deployment_id)
        if d is None:
            raise KeyError("部署不存在")
        conn = s.get(Connection, d.connection_id) if d.connection_id else None
        owner = s.get(User, d.owner_id)
        tables = (
            s.query(TableSpec).filter_by(deployment_id=deployment_id)
            .order_by(TableSpec.id).all()
        )
        pages = (
            s.query(PageSpec).filter_by(deployment_id=deployment_id)
            .order_by(PageSpec.id).all()
        )
        menus = (
            s.query(DeploymentMenu).filter_by(deployment_id=deployment_id)
            .order_by(DeploymentMenu.order_index, DeploymentMenu.id).all()
        )
        cruds = (
            s.query(CrudConfig).filter_by(deployment_id=deployment_id)
            .order_by(CrudConfig.id).all()
        )
        flows = (
            s.query(FlowSpec).filter_by(deployment_id=deployment_id)
            .order_by(FlowSpec.id).all()
        )
        # 0.13.0 M7-4：界面配置三节（仅导出有配置的表）
        form_fields = (
            s.query(FormFieldConfig).filter_by(deployment_id=deployment_id)
            .order_by(FormFieldConfig.table_name, FormFieldConfig.field_name).all()
        )
        form_options = (
            s.query(FormOptionItem).filter_by(deployment_id=deployment_id)
            .order_by(FormOptionItem.table_name, FormOptionItem.field_name,
                      FormOptionItem.order_index).all()
        )
        form_layouts = (
            s.query(FormLayout).filter_by(deployment_id=deployment_id)
            .order_by(FormLayout.table_name, FormLayout.page_kind).all()
        )
        logs = (
            s.query(ActivityLog)
            .filter(ActivityLog.deployment_id == deployment_id,
                    ActivityLog.status == "committed")
            .order_by(ActivityLog.occurred_at, ActivityLog.id)
            .all()
        )
        users = {u.id: u for u in s.query(User).all()}

        def actor_display(uid):
            if uid is None:
                return "系统"
            u = users.get(uid)
            return (u.display_name or u.username) if u else f"#{uid}"

        package = {
            "format": EXPORT_FORMAT,
            "version": EXPORT_VERSION,
            "exported_at": datetime.now().replace(microsecond=0).isoformat(sep=" "),
            "deployment": {
                "id": d.id,
                "biz_name": d.biz_name,
                "purpose": d.purpose,
                "data_mode": d.data_mode,
                "db_name": d.db_name,
                "status": d.status,
                "entry_path": d.entry_path,
                "seed_data": bool(d.seed_data),
                "created_at": d.created_at.isoformat(sep=" ") if d.created_at else "",
                "owner": (
                    {"id": owner.id, "username": owner.username,
                     "display_name": owner.display_name or owner.username}
                    if owner else None
                ),
                "connection": (
                    {"name": conn.name, "dialect": conn.dialect}
                    if conn else None
                ),
            },
            "table_specs": [
                {
                    "id": t.id,
                    "name": t.name,
                    "biz_name": t.biz_name,
                    "columns": _jloads(t.columns_json, []),
                    "ddl_sql": t.ddl_sql,
                    "status": t.status,
                }
                for t in tables
            ],
            "page_specs": [
                {
                    "id": p.id,
                    "kind": p.kind,
                    "access": p.access,
                    "table_id": p.table_id,
                    "title": p.title,
                    "layout": _jloads(p.layout_json, {}),
                    "flow_id": p.flow_id,
                }
                for p in pages
            ],
            "deployment_menus": [
                {
                    "id": m.id,
                    "parent_id": m.parent_id,
                    "label": m.label,
                    "kind": m.kind,
                    "ref_id": m.ref_id,
                    "order_index": m.order_index,
                }
                for m in menus
            ],
            "crud_configs": [
                {
                    "id": c.id,
                    "table_id": c.table_id,
                    "api_config": _jloads(c.api_config_json, {}),
                    "ui_config": _jloads(c.ui_config_json, {}),
                }
                for c in cruds
            ],
            "flow_specs": [
                {
                    "id": f.id,
                    "name": f.name,
                    "steps": f.steps,
                    "version": f.version,
                    "progress": _jloads(f.progress_json, {}),
                }
                for f in flows
            ],
            "form_field_configs": [
                {
                    "table_name": r.table_name,
                    "field_name": r.field_name,
                    "control_type": r.control_type,
                    "time_granularity": r.time_granularity,
                    "readonly": bool(r.readonly),
                    "hidden": bool(r.hidden),
                    "hint": r.hint,
                }
                for r in form_fields
            ],
            "form_option_items": [
                {
                    "table_name": r.table_name,
                    "field_name": r.field_name,
                    "value": r.value,
                    "label": r.label,
                    "order_index": r.order_index,
                }
                for r in form_options
            ],
            "form_layouts": [
                {
                    "table_name": r.table_name,
                    "page_kind": r.page_kind,
                    "columns_per_row": r.columns_per_row,
                    "order": _jloads(r.order_json, []),
                    "groups": _jloads(r.groups_json, []),
                }
                for r in form_layouts
            ],
            "activity_logs": [
                {
                    "id": lg.id,
                    "occurred_at": lg.occurred_at.isoformat(sep=" ") if lg.occurred_at else "",
                    "actor_user_id": lg.actor_user_id,
                    "actor_display": actor_display(lg.actor_user_id),
                    "action": lg.action,
                    "status": lg.status,
                    "object_kind": lg.object_kind,
                    "object_table": lg.object_table,
                    "object_pk": lg.object_pk,
                    "object_label": lg.object_label,
                    "changes": lg.changes,
                    "source_ip": lg.source_ip,
                }
                for lg in logs
            ],
        }
        package["counts"] = {
            "table_specs": len(package["table_specs"]),
            "page_specs": len(package["page_specs"]),
            "deployment_menus": len(package["deployment_menus"]),
            "crud_configs": len(package["crud_configs"]),
            "flow_specs": len(package["flow_specs"]),
            "activity_logs": len(package["activity_logs"]),
            "form_field_configs": len(package["form_field_configs"]),
            "form_option_items": len(package["form_option_items"]),
            "form_layouts": len(package["form_layouts"]),
        }
        return package
