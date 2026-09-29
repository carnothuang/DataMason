"""访客公开页（M3-5，DR-04）：匿名可访问的 `public_form` 页面。

- 访客不是成员角色，是「访问者」：只能提交到公开表单，看不到任何菜单与数据。
- 提交走 CRUD 写路径（列白名单/类型还原/两阶段日志钩子）：归属列记系统（created_by=0），
  activity_logs 记 action=create 且 actor_user_id=NULL（显示为「系统」）。
- 仅 `page_specs.access=public` 且 `kind=public_form` 的页面开放；部署需运行中。
"""
from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from werkzeug.exceptions import Conflict

from datamason.audit.service import AuditSession
from datamason.crud import engine as crud_engine
from datamason.crud import runtime as crud_runtime
from datamason.crud.engine import CrudRegistry
from datamason.models import Deployment, PageSpec, TableSpec

VISITOR_ACTOR_ID = 0  # 归属列「系统/访客」占位（created_by NOT NULL，访客无 users.id）
_OWNERSHIP_COLS = crud_engine._OWNERSHIP_COLS


class PublicError(ValueError):
    """公开页校验/提交错误 → 400。"""


def _session(app):
    return app.extensions["datamason_session_factory"]()


def _context(app, deployment_id: int, page_id: int):
    with _session(app) as s:
        d = s.get(Deployment, deployment_id)
        if d is None:
            raise KeyError("部署不存在")
        if d.status != "running":
            raise Conflict(description="该页面暂不可用（部署未运行）")
        p = s.get(PageSpec, page_id)
        if p is None or p.deployment_id != deployment_id:
            raise KeyError("页面不存在")
        if p.access != "public" or p.kind != "public_form":
            raise PublicError("该页面不是公开提交页")
        ts = s.get(TableSpec, p.table_id) if p.table_id else None
        if ts is None:
            raise PublicError("公开页未绑定数据表")
        return d, p, ts


def _field_columns(ts: TableSpec) -> list[dict]:
    return [
        {
            "name": c["name"],
            "biz_name": c.get("biz_name") or c["name"],
            "type": c.get("type") or "",
            "length": c.get("length"),
            "nullable": bool(c.get("nullable", True)),
            "is_pk": bool(c.get("is_pk")),
            "is_ownership": bool(c.get("is_ownership")),
        }
        for c in ts.columns
    ]


def _public_layout(layout: dict, allowed: set[str]) -> dict:
    """按公开页字段白名单裁剪布局（最小暴露：不含他表/已删字段）。"""
    order = [n for n in (layout.get("order") or []) if n in allowed]
    groups = []
    for g in layout.get("groups") or []:
        names = [n for n in (g.get("field_names") or []) if n in allowed]
        if names:
            groups.append({"title": g.get("title", ""), "field_names": names})
    return {
        "columns_per_row": layout.get("columns_per_row", 1) or 1,
        "order": order,
        "groups": groups,
    }


def get_page(app, deployment_id: int, page_id: int) -> dict:
    d, p, ts = _context(app, deployment_id, page_id)
    fields = [
        c for c in _field_columns(ts)
        if not c["is_ownership"] and not c["is_pk"]
    ]
    result = {
        "deployment_id": deployment_id,
        "biz_name": d.biz_name,
        "page_id": p.id,
        "title": p.title,
        "kind": p.kind,
        "table": ts.name,
        "fields": fields,
    }
    allowed = {f["name"] for f in fields}
    # 0.13.0 M7-4-2：下发公开表单配置（仅本页字段与候选项）；缺省时响应与升级前完全一致
    try:
        from datamason.forms import service as form_service

        cfg = form_service.get_form_config(
            app, deployment_id, ts.name, page_kind="public")
    except Exception:  # noqa: BLE001 配置不可用 → 默认映射（不阻断公开页）
        _apply_ui_subset(app, deployment_id, ts.name, allowed, fields, result)
        return result
    if cfg.get("degraded"):
        _apply_ui_subset(app, deployment_id, ts.name, allowed, fields, result)
        return result

    cfg_by = {f["field_name"]: f for f in cfg["fields"]}
    for f in fields:
        c = cfg_by.get(f["name"])
        if c and (c.get("configured") or c.get("options")):
            f["control_type"] = c["control_type"]
            f["time_granularity"] = c["time_granularity"]
            f["readonly"] = c["readonly"]
            f["hidden"] = c["hidden"]
            f["hint"] = c["hint"]
            f["options"] = c["options"]
    layout = cfg.get("layout") or {}
    if (layout.get("order") or layout.get("groups")
            or (layout.get("columns_per_row") or 1) != 1):
        result["layout"] = _public_layout(layout, allowed)

    _apply_ui_subset(app, deployment_id, ts.name, allowed, fields, result)
    return result


def _apply_ui_subset(app, deployment_id: int, table_name: str,
                     allowed: set[str], fields: list[dict], result: dict) -> None:
    """0.15.0 M9-8-4：公开页仅消费表级 UI 的安全子集——列显隐/列序。

    - `list.visible_columns` 非空时：清单外字段在公开表单隐藏，清单顺序作为公开页排序；
    - 不开放区块画布/拖拽/详情等任何写配置入口（公开页本就只读提交）；
    - 表级 UI 未配置/损坏 → 零变化（最小暴露原则）。
    """
    try:
        from datamason.workspace import service as ws_service

        spec = ws_service._table_ui_spec(app, deployment_id, table_name)
    except Exception:  # noqa: BLE001 配置不可用 → 不影响公开页
        return
    if spec is None:
        return
    visible = [n for n in (spec.list.visible_columns or []) if n in allowed]
    if not visible:
        return
    visible_set = set(visible)
    for f in fields:
        if f["name"] not in visible_set:
            f["hidden"] = True
    layout = dict(result.get("layout") or {})
    layout["order"] = visible
    result["layout"] = layout


def submit(app, deployment_id: int, page_id: int, source_ip: str = "") -> dict:
    d, p, ts = _context(app, deployment_id, page_id)
    from flask import request

    payload = request.get_json(force=True, silent=True) or {}
    if not isinstance(payload, dict):
        raise PublicError("提交内容格式错误")

    engine = crud_runtime._biz_engine(app, d)
    try:
        from datamason.forms import service as form_service

        registry = CrudRegistry(
            engine, tables=[ts.name], current_user_id=VISITOR_ACTOR_ID, owner_only=False,
            value_validator=form_service.build_validator(
                app, deployment_id, ts.name, page_kind="public"),
        )
        table = registry.table(ts.name)
        # 必填校验（NOT NULL 业务列）：给出业务名提示
        spec_cols = {c["name"]: c for c in _field_columns(ts)}
        for col in table.columns:
            if col.name in _OWNERSHIP_COLS or col.primary_key:
                continue
            if col.server_default is not None:
                continue
            if col.nullable is False:
                raw = payload.get(col.name)
                if raw is None or (isinstance(raw, str) and raw.strip() == ""):
                    label = spec_cols.get(col.name, {}).get("biz_name") or col.name
                    raise PublicError(f"请填写「{label}」")
        audit = AuditSession(app, deployment_id, None, source_ip)
        try:
            result = crud_engine.create_row(registry, ts.name, audit=audit)
        except IntegrityError as exc:
            raise PublicError(f"提交失败：{_friendly_integrity(exc)}") from exc
        return {"ok": True, "id": result.get("id")}
    finally:
        engine.dispose()


def _friendly_integrity(exc: IntegrityError) -> str:
    text = str(getattr(exc, "orig", exc))
    if "UNIQUE" in text.upper():
        return "内容与已有记录重复，请检查后重试"
    return "数据不满足表约束"
