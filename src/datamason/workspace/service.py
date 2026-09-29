"""工作台运行时的领域组装（M2-2 菜单树 / M2-5 页面载荷 / M2-6 日志富化）。

`routes.py` 只做 HTTP；本模块承接菜单树构造、页面「动作 + 数据范围」载荷、
CRUD 元数据富化、操作日志业务名富化与 CSV 序列化等纯领域逻辑。
不 import Flask 的 request / g / session；成员与授权判定复用 `server.members.service`。
"""
from __future__ import annotations

import csv
import io

from datamason.members import service as member_service
from datamason.models import CrudConfig, Deployment, DeploymentMenu, PageSpec, TableSpec
from datamason.workspace.layout import (
    PAGE_LAYOUT_VERSION,
    PageLayoutSpec,
    TableUiConfig,
    UI_CONFIG_VERSION,
)


def menu_tree(menus: list[DeploymentMenu]) -> list[dict]:
    """把平铺菜单（按 id 排序）构造成树：parent_id 指向本集合内的菜单。

    孤儿（parent 不在集合内）按根节点处理，避免丢失。
    """
    nodes = {
        m.id: {
            "id": m.id,
            "label": m.label,
            "kind": m.kind,
            "ref_id": m.ref_id,
            "children": [],
        }
        for m in menus
    }
    roots: list[dict] = []
    for m in menus:
        node = nodes[m.id]
        parent = nodes.get(m.parent_id)
        if m.parent_id and parent is not None:
            parent["children"].append(node)
        else:
            roots.append(node)
    return roots


def _page_access(role: str, access_map, table_name: str | None) -> tuple[list[str], str]:
    """M5-C3（DR-30）：页面背后表的允许动作与数据范围，前端据此裁剪按钮。"""
    if role in ("creator", "admin"):
        return sorted(member_service.ACTION_KEYS), "all"
    if access_map and table_name and table_name in access_map:
        info = access_map[table_name]
        return sorted(info["actions"]), info["data_scope"]
    return [], "own"


def stopped_payload(d: Deployment, role: str) -> dict:
    """停止态工作台载荷（M3-1）：不返回菜单/页面，只给「系统已停止」提示页所需字段。"""
    return {
        "deployment_id": d.id,
        "biz_name": d.biz_name,
        "status": "stopped",
        "data_mode": d.data_mode,
        "entry_path": d.entry_path,
        "role": role,
        "menus": [],
        "pages": [],
    }


def build_payload(s, d: Deployment, role: str, uid: int) -> dict:
    """组装 GET /app/<id> 的载荷：菜单树（按角色裁剪）+ 页面卡 + 角色。"""
    deployment_id = d.id
    menus = (
        s.query(DeploymentMenu)
        .filter_by(deployment_id=deployment_id)
        .order_by(DeploymentMenu.order_index, DeploymentMenu.id)
        .all()
    )
    if role == "operator":
        # 功能级裁剪（DR-02）：操作员可见菜单=自己的授权集合（含祖先分组）
        granted = member_service.granted_menu_ids(s, d, uid)
        visible = member_service.visible_menu_ids(menus, granted)
        menus = [m for m in menus if m.id in visible]

    visible_ref_ids = {m.ref_id for m in menus if m.ref_id}
    pages = (
        s.query(PageSpec)
        .filter_by(deployment_id=deployment_id)
        .order_by(PageSpec.id)
        .all()
    )
    if role == "operator":
        # 页面同菜单一起裁剪：只保留被授权菜单指向的页面
        pages = [p for p in pages if p.id in visible_ref_ids]
    tables = {
        t.id: t for t in s.query(TableSpec).filter_by(deployment_id=deployment_id).all()
    }
    access_map = member_service.table_access_map(s, d, uid) if role == "operator" else None

    pages_payload = []
    for p in pages:
        tname = tables[p.table_id].name if (p.table_id and p.table_id in tables) else None
        actions, scope = _page_access(role, access_map, tname)
        pages_payload.append({
            "id": p.id,
            "kind": p.kind,
            "access": p.access,
            "title": p.title,
            "flow_id": p.flow_id,
            "table": tname,
            "table_biz_name": tables[p.table_id].biz_name
            if (p.table_id and p.table_id in tables)
            else None,
            "actions": actions,
            "data_scope": scope,
        })
    return {
        "deployment_id": deployment_id,
        "biz_name": d.biz_name,
        "status": d.status,
        "data_mode": d.data_mode,
        "entry_path": d.entry_path,
        "role": role,
        "menus": menu_tree(menus),
        "pages": pages_payload,
    }


def enrich_meta(
    s,
    raw: dict,
    deployment_id: int,
    role: str | None,
    uid: int,
    deployment: Deployment | None,
) -> dict:
    """富化 CRUD 元数据：业务名列/归属列标记/长度（table_specs）+ 按表动作与数据范围。

    role=None（API key 服务身份）时不做动作裁剪。
    """
    specs = {
        t.name: {c["name"]: c for c in t.columns}
        for t in s.query(TableSpec).filter_by(deployment_id=deployment_id).all()
    }
    for table_name, info in raw.items():
        spec_cols = specs.get(table_name, {})
        for c in info["columns"]:
            sc = spec_cols.get(c["name"], {})
            c["biz_name"] = sc.get("biz_name") or c["name"]
            c["is_ownership"] = bool(sc.get("is_ownership"))
            # 类型取约定语义类型（BOOLEAN 在 SQLite 反射为 INTEGER 会丢语义）
            if sc.get("type"):
                c["type"] = sc["type"]
            if c["length"] is None:
                c["length"] = sc.get("length")

    if role in ("creator", "admin"):
        for info in raw.values():
            info["actions"] = sorted(member_service.ACTION_KEYS)
            info["data_scope"] = "all"
    elif role == "operator" and deployment is not None:
        access = member_service.table_access_map(s, deployment, uid)
        for table_name, info in raw.items():
            a = access.get(table_name)
            info["actions"] = sorted(a["actions"]) if a else []
            info["data_scope"] = a["data_scope"] if a else "own"
    return raw


def enrich_activity_logs(s, deployment_id: int, result: dict) -> dict:
    """给日志行补业务名（object_biz_name），供日志页人话展示。"""
    biz = {
        t.name: t.biz_name
        for t in s.query(TableSpec).filter_by(deployment_id=deployment_id).all()
    }
    for lg in result["logs"]:
        lg["object_biz_name"] = biz.get(lg["object_table"]) or lg["object_table"]
    return result


def rows_to_csv(columns: list[str], rows: list[dict]) -> str:
    """列表行 → CSV 文本（UTF-8 BOM 由调用方添加，便于 Excel 直开）。"""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    for r in rows:
        writer.writerow(["" if r.get(c) is None else r.get(c) for c in columns])
    return buf.getvalue()


# ---------------- 0.15.0 M9-6 页面布局与表级 UI 配置 ----------------

_LAYOUT_ERR = "布局配置错误"


def _table_spec(app, deployment_id: int, table_name: str) -> TableSpec | None:
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        return (
            s.query(TableSpec)
            .filter_by(deployment_id=deployment_id, name=table_name)
            .first()
        )


def _page_spec(app, deployment_id: int, page_id: int) -> PageSpec | None:
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        return (
            s.query(PageSpec)
            .filter_by(deployment_id=deployment_id, id=page_id)
            .first()
        )


def _validate_page_layout_against_schema(
    spec: PageLayoutSpec, table_specs: dict[str, TableSpec]
) -> None:
    """区块引用的表/列存在性 + 统计口径数值列校验（结构与枚举已在 layout.py 校验）。"""
    for b in spec.blocks:
        if b.x_component in ("table", "stat", "detail"):
            tname = (b.props.get("table") or "").strip()
            ts = table_specs.get(tname)
            if ts is None:
                raise ValueError(f"区块引用了不存在的表：{tname}")
            col_names = {c["name"] for c in ts.columns}
            if b.x_component == "table":
                for cname in b.props.get("columns") or []:
                    if cname not in col_names:
                        raise ValueError(f"列表区块引用了不存在的列：{tname}.{cname}")
            elif b.x_component == "stat":
                agg = b.props.get("aggregation")
                col = b.props.get("column")
                if agg in ("sum", "avg"):
                    if not col or col not in col_names:
                        raise ValueError(f"统计区块聚合列不存在：{tname}.{col}")
                    ct = next((c for c in ts.columns if c["name"] == col), None)
                    if ct is None or (ct.get("type") or "").upper() not in (
                        "INTEGER", "BIGINT", "DECIMAL",
                    ):
                        raise ValueError(f"统计聚合列必须是数值列：{tname}.{col}")


def get_page_layout(app, deployment_id: int, page_id: int) -> dict:
    """读页面级布局：`"{}"` 或解析失败 → 返回空布局（默认派生，零配置零变化）。"""
    page = _page_spec(app, deployment_id, page_id)
    if page is None:
        raise KeyError(f"页面不存在：{page_id}")
    raw = page.layout_json or "{}"
    try:
        spec = PageLayoutSpec.model_validate_json(raw)
        return {"version": PAGE_LAYOUT_VERSION, "blocks": spec.model_dump()["blocks"],
                "degraded": False}
    except Exception:  # noqa: BLE001 损坏配置降级为空布局
        return {"version": PAGE_LAYOUT_VERSION, "blocks": [], "degraded": True}


def save_page_layout(app, deployment_id: int, page_id: int, payload: dict,
                     actor_user_id: int = 0) -> dict:
    """保存页面级布局：pydantic 校验 + 表/列存在性 + 单事务写 + 审计。"""
    try:
        spec = PageLayoutSpec.model_validate(payload)
    except Exception as exc:  # noqa: BLE001 pydantic ValidationError 统一包装
        raise ValueError(f"{_LAYOUT_ERR}：{_first_error(exc)}") from exc
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        page = s.query(PageSpec).filter_by(
            deployment_id=deployment_id, id=page_id
        ).first()
        if page is None:
            raise KeyError(f"页面不存在：{page_id}")
        specs = {
            t.name: t for t in s.query(TableSpec).filter_by(deployment_id=deployment_id).all()
        }
        try:
            _validate_page_layout_against_schema(spec, specs)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        page.layout_json = spec.model_dump_json()
        s.commit()
        page_id_out = page_id
    _audit_layout(app, deployment_id, actor_user_id, "保存页面布局", {
        "page_id": page_id_out, "blocks": len(spec.blocks),
    })
    return {"saved": True, "page_id": page_id_out, "blocks": len(spec.blocks)}


def reset_page_layout(app, deployment_id: int, page_id: int,
                      actor_user_id: int = 0) -> dict:
    """还原页面布局为默认：清回 `"{}"`。"""
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        page = s.query(PageSpec).filter_by(
            deployment_id=deployment_id, id=page_id
        ).first()
        if page is None:
            raise KeyError(f"页面不存在：{page_id}")
        page.layout_json = "{}"
        s.commit()
    _audit_layout(app, deployment_id, actor_user_id, "还原页面布局", {"page_id": page_id})
    return {"reset": True, "page_id": page_id}


def _table_ui_spec(app, deployment_id: int, table_name: str) -> TableUiConfig | None:
    """读表级 UI 配置（未配置返回 None）。"""
    ts = _table_spec(app, deployment_id, table_name)
    if ts is None:
        return None
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        cc = (
            s.query(CrudConfig)
            .filter_by(deployment_id=deployment_id, table_id=ts.id)
            .first()
        )
    if cc is None or not (cc.ui_config_json or "").strip():
        return None
    raw = cc.ui_config_json.strip()
    if raw in ("{}", ""):
        return None  # 占位空配置 = 未配置（零配置零变化）
    try:
        return TableUiConfig.model_validate_json(raw)
    except Exception:  # noqa: BLE001 损坏配置视为未配置
        return None


def get_table_ui_config(app, deployment_id: int, table_name: str) -> dict:
    """读表级 UI 配置：未配置/损坏 → 返回默认空配置（零配置零变化）。"""
    cfg = _table_ui_spec(app, deployment_id, table_name)
    if cfg is None:
        return {
            "version": UI_CONFIG_VERSION,
            "list": {},
            "detail": {},
            "degraded": False,
        }
    return {**cfg.model_dump(exclude_none=True), "degraded": False}


def save_table_ui_config(app, deployment_id: int, table_name: str, payload: dict,
                         actor_user_id: int = 0) -> dict:
    """保存表级 UI 配置：pydantic 校验 + 列存在性 + 单事务写 + 审计。"""
    try:
        cfg = TableUiConfig.model_validate(payload)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"{_LAYOUT_ERR}：{_first_error(exc)}") from exc
    ts = _table_spec(app, deployment_id, table_name)
    if ts is None:
        raise ValueError(f"数据表不存在：{table_name}")
    col_names = {c["name"] for c in ts.columns}
    for cname in cfg.list.visible_columns:
        if cname not in col_names:
            raise ValueError(f"列表配置引用了不存在的列：{table_name}.{cname}")
    for f in cfg.detail.fields:
        if f not in col_names:
            raise ValueError(f"详情配置引用了不存在的列：{table_name}.{f}")
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        cc = (
            s.query(CrudConfig)
            .filter_by(deployment_id=deployment_id, table_id=ts.id)
            .first()
        )
        if cc is None:
            cc = CrudConfig(
                deployment_id=deployment_id, table_id=ts.id,
                api_config_json="{}", ui_config_json="{}",
            )
            s.add(cc)
        cc.ui_config_json = cfg.model_dump_json()
        s.commit()
    _audit_layout(app, deployment_id, actor_user_id, "保存表级界面配置", {
        "table": table_name,
        "list_columns": len(cfg.list.visible_columns),
    })
    return {"saved": True, "table": table_name}


def _audit_layout(app, deployment_id: int, actor_user_id: int, summary: str,
                  changes: dict) -> None:
    """布局配置审计（元数据库单事务，失败不影响主流程）。"""
    from datamason.audit import service as audit_service

    try:
        audit_service.log_system(
            app, deployment_id, "ui_config",
            actor_user_id=actor_user_id or None,
            object_kind="page_layout",
            object_table=changes.get("table") or "",
            object_label=summary,
            changes=changes,
        )
    except Exception:  # noqa: BLE001 审计旁路失败不阻断
        pass


def _first_error(exc: Exception) -> str:
    errors = getattr(exc, "errors", None)
    if callable(errors):
        try:
            raw = errors()
            if raw and isinstance(raw, list) and "msg" in raw[0]:
                return str(raw[0]["msg"])
        except Exception:  # noqa: BLE001
            pass
    return str(exc)

