"""结构演化（0.11.0 M5-A，DR-27/DR-31）：对**创建模式自建库**增表/删表。

生成即提案（不确认不落库），直连（link）模式一律拒绝（外部库红线）。
- 增表：pydantic 强校验 → 外键指向已存在真实主键 → 归属列注入 → 建表 → 写
  table_specs/page_specs/deployment_menus/crud_configs（与首次部署产物同构）。
- 删表：先算影响面（行数/外键引用/页面/菜单/授权/流程/报表/种子）；被引用或被流程
  引用则拒绝（保守，沿用列迁移保护哲学）；确认后级联清理 + 物理 DROP。
"""
from __future__ import annotations

import json

from sqlalchemy import create_engine, func, inspect, select, text

from datamason.audit.service import log_system
from datamason.dataaccess.manager import execute_ddl
from datamason.ddl.ddl_builder import (
    _map_type,
    build_indexes_ddl,
    build_table_ddl,
    inject_ownership_columns,
)
from datamason.ddl.table_spec import TableSpecModel
from datamason.models import (
    Attachment,
    Connection,
    CrudConfig,
    Deployment,
    DeploymentMenu,
    DeploymentMenuGrant,
    DeploymentSeedRow,
    FlowSpec,
    PageSpec,
    TableSpec,
)


class StructureError(RuntimeError):
    pass


def _session(app):
    return app.extensions["datamason_session_factory"]()


def _biz_engine(app, deployment: Deployment):
    cipher = app.extensions["cipher"]
    with _session(app) as s:
        conn = s.get(Connection, deployment.connection_id)
        if conn is None:
            raise StructureError("部署没有关联数据连接")
        uri = cipher.decrypt(conn.conn_uri)
        dialect = conn.dialect
    return create_engine(uri, future=True), dialect


def _assert_editable(deployment: Deployment) -> None:
    if (getattr(deployment, "data_mode", "create") or "create") == "link":
        raise StructureError(
            "该部署为直连模式（外部库结构受保护，DR-27）。DataMason 不会对外部库执行 DDL"
        )


def _load_deployment(app, deployment_id: int) -> Deployment:
    with _session(app) as s:
        d = s.get(Deployment, deployment_id)
        if d is None:
            raise KeyError("部署不存在")
        return d


def _existing_models(app, deployment_id: int):
    """返回 {表名: TableSpecModel} 与 {表名: table_specs.id}。"""
    from datamason.deploy.editor import _enrich_from_db, _to_model

    with _session(app) as s:
        d = s.get(Deployment, deployment_id)
        if d is None:
            raise KeyError("部署不存在")
        rows = (
            s.query(TableSpec).filter_by(deployment_id=deployment_id)
            .order_by(TableSpec.id).all()
        )
        snapshot = [(r.id, r.name, r.biz_name, r.columns_json) for r in rows]
    engine, _dialect = _biz_engine(app, d)
    models: dict[str, TableSpecModel] = {}
    id_map: dict[str, int] = {}
    try:
        for row_id, name, biz_name, raw in snapshot:
            cols = _enrich_from_db(engine, name, json.loads(raw or "[]"))
            models[name] = _to_model(name, biz_name, cols)
            id_map[name] = row_id
    finally:
        engine.dispose()
    return models, id_map


def _rich_columns(model: TableSpecModel, dialect: str) -> list[dict]:
    out = []
    for c in model.columns:
        out.append({
            "name": c.name,
            "biz_name": c.biz_name or c.name,
            "type": c.type,
            "length": c.length,
            "nullable": c.nullable,
            "is_pk": c.is_pk,
            "autoincrement": c.autoincrement,
            "is_fk": c.is_fk,
            "ref_table": c.ref_table,
            "ref_column": c.ref_column,
            "is_ownership": c.is_ownership,
            "db_type": _map_type(c, dialect),
        })
    return out


def _column_public(c) -> dict:
    return {
        "name": c.name,
        "biz_name": c.biz_name,
        "type": c.type,
        "length": c.length,
        "nullable": c.nullable,
        "is_pk": c.is_pk,
        "is_fk": c.is_fk,
        "ref_table": c.ref_table,
        "ref_column": c.ref_column,
        "is_ownership": c.is_ownership,
    }


# ---------------- 增表 ----------------

def _parse_new_table(spec: dict) -> TableSpecModel:
    try:
        model = TableSpecModel(**spec)
    except Exception as exc:  # pydantic.ValidationError 等
        raise StructureError(f"表规格校验失败：{exc}") from exc
    return model


def _validate_new_table(app, deployment_id: int, model: TableSpecModel,
                        existing: dict[str, TableSpecModel]) -> None:
    if model.name in existing:
        raise StructureError(f"表 {model.name} 已存在")
    # 外键必须指向「已存在表的真实主键」或本表主键（单表新增）
    for c in model.columns:
        if not c.is_fk:
            continue
        if c.ref_table == model.name:
            ref = model
        else:
            ref = existing.get(c.ref_table or "")
        if ref is None:
            raise StructureError(f"外键列 {c.name} 引用的表 {c.ref_table} 不存在")
        pks = {pc.name for pc in ref.columns if pc.is_pk}
        if c.ref_column not in pks:
            raise StructureError(
                f"外键列 {c.name} 必须引用 {c.ref_table} 的主键（当前 ref_column={c.ref_column}）"
            )


def propose_add_table(app, deployment_id: int, spec: dict) -> dict:
    """增表提案：只算不执行。返回将执行的 DDL、将生成的页面/菜单计划、关系。"""
    deployment = _load_deployment(app, deployment_id)
    _assert_editable(deployment)
    model = _parse_new_table(spec)
    model = inject_ownership_columns(model)
    existing, _ = _existing_models(app, deployment_id)
    _validate_new_table(app, deployment_id, model, existing)

    engine, dialect = _biz_engine(app, deployment)
    try:
        # 真实库同名兜底（table_specs 之外的物理表）
        insp = inspect(engine)
        if model.name in set(insp.get_table_names()):
            raise StructureError(f"数据库已存在同名表 {model.name}（元数据未登记），请换名")
    finally:
        engine.dispose()

    statements = [build_table_ddl(model, dialect)] + build_indexes_ddl(model, dialect)
    relations = [
        {"column": c.name, "ref_table": c.ref_table, "ref_column": c.ref_column}
        for c in model.columns if c.is_fk
    ]
    return {
        "table": model.name,
        "biz_name": model.biz_name,
        "columns": [_column_public(c) for c in model.columns],
        "relations": relations,
        "statements": statements,
        "pages": [{"kind": "crud_list", "access": "member", "title": model.biz_name or model.name}],
        "valid": True,
    }


def add_table(app, deployment_id: int, spec: dict, actor_user_id: int | None) -> dict:
    """增表确认执行：建表 + 写元数据（页面/菜单/配置）+ structure 日志。"""
    deployment = _load_deployment(app, deployment_id)
    _assert_editable(deployment)
    model = _parse_new_table(spec)
    model = inject_ownership_columns(model)
    existing, _ = _existing_models(app, deployment_id)
    _validate_new_table(app, deployment_id, model, existing)

    engine, dialect = _biz_engine(app, deployment)
    try:
        insp = inspect(engine)
        if model.name in set(insp.get_table_names()):
            raise StructureError(f"数据库已存在同名表 {model.name}（元数据未登记），请换名")
        statements = [build_table_ddl(model, dialect)] + build_indexes_ddl(model, dialect)
        execute_ddl(engine, statements)
        columns = _rich_columns(model, dialect)
    except StructureError:
        engine.dispose()
        raise
    except Exception as exc:  # noqa: BLE001
        engine.dispose()
        raise StructureError(f"建表执行失败：{type(exc).__name__}: {exc}") from exc
    engine.dispose()

    with _session(app) as s:
        ts = TableSpec(
            deployment_id=deployment_id, name=model.name, biz_name=model.biz_name or "",
            columns_json=json.dumps(columns, ensure_ascii=False),
            ddl_sql="\n".join(statements), status="applied",
        )
        s.add(ts)
        s.flush()
        page = PageSpec(
            deployment_id=deployment_id, kind="crud_list", access="member",
            table_id=ts.id, title=model.biz_name or model.name, layout_json="{}",
        )
        s.add(page)
        s.flush()
        max_order = (
            s.query(func.max(DeploymentMenu.order_index))
            .filter_by(deployment_id=deployment_id).scalar()
        )
        menu = DeploymentMenu(
            deployment_id=deployment_id, parent_id=None, label=model.biz_name or model.name,
            kind="table", ref_id=page.id, order_index=(max_order or 0) + 1,
        )
        s.add(menu)
        s.add(CrudConfig(deployment_id=deployment_id, table_id=ts.id,
                         api_config_json="{}", ui_config_json="{}"))
        s.commit()
        page_id, menu_id = page.id, menu.id

    log_system(
        app, deployment_id, "structure", actor_user_id=actor_user_id,
        object_kind="table", object_table=model.name, object_pk=str(page_id),
        object_label=f"新增表：{model.biz_name or model.name}",
        changes={"action": "add_table", "table": model.name,
                 "columns": [c.name for c in model.columns]},
    )
    return {
        "table": model.name, "columns": [_column_public(c) for c in model.columns],
        "page_id": page_id, "menu_id": menu_id, "statements": statements,
    }


# ---------------- 删表 ----------------

def _count_rows(engine, dialect: str, table_name: str) -> int:
    if not table_name.replace("_", "").isalnum() or not table_name[0].isalpha() and table_name[0] != "_":
        raise StructureError(f"非法表名：{table_name}")
    if dialect == "mysql":
        q = f"`{table_name}`"
    elif dialect == "postgresql":
        q = f'"{table_name}"'
    else:
        q = table_name
    with engine.connect() as conn:
        return conn.execute(text(f"SELECT COUNT(*) FROM {q}")).scalar_one()


def analyze_drop_table(app, deployment_id: int, table_name: str) -> dict:
    """影响面计算（不执行）：被引用/页面/菜单/授权/流程/报表/种子/行数 + 是否阻断。"""
    deployment = _load_deployment(app, deployment_id)
    _assert_editable(deployment)
    models, id_map = _existing_models(app, deployment_id)
    if table_name not in models:
        raise KeyError(f"表 {table_name} 不存在")

    engine, dialect = _biz_engine(app, deployment)
    try:
        row_count = _count_rows(engine, dialect, table_name)
    except Exception:  # noqa: BLE001 物理表可能缺失：按 0 计，仍允许清理元数据
        row_count = 0
    finally:
        engine.dispose()

    with _session(app) as s:
        referenced_by = []
        for name, m in models.items():
            for c in m.columns:
                if c.is_fk and c.ref_table == table_name:
                    referenced_by.append({"table": name, "column": c.name})
        target_id = id_map[table_name]
        pages = s.query(PageSpec).filter_by(deployment_id=deployment_id).all()
        target_pages = [p for p in pages if p.table_id == target_id]
        page_ids = {p.id for p in target_pages}
        # 报表 SQL 可能引用该表（保守：出现表名即提示）
        reports = [
            p.title for p in pages
            if p.kind == "dashboard" and table_name in (p.layout_json or "")
        ]
        menus = (
            s.query(DeploymentMenu)
            .filter(DeploymentMenu.deployment_id == deployment_id,
                    DeploymentMenu.ref_id.in_(page_ids or {-1}))
            .all()
        )
        menu_ids = {m.id for m in menus}
        grants = 0
        if menu_ids:
            grants = (
                s.query(DeploymentMenuGrant)
                .filter(DeploymentMenuGrant.menu_id.in_(menu_ids)).count()
            )
        flows = s.query(FlowSpec).filter_by(deployment_id=deployment_id).all()
        flow_hits = []
        for f in flows:
            if any(st.get("page_id") in page_ids for st in f.steps):
                flow_hits.append(f.name)
        seeds = (
            s.query(DeploymentSeedRow)
            .filter_by(deployment_id=deployment_id, table_name=table_name).count()
        )

    blocked = False
    reason = ""
    if referenced_by:
        blocked = True
        refs = "、".join(f"{r['table']}.{r['column']}" for r in referenced_by)
        reason = f"该表被其他表外键引用（{refs}），请先处理引用方再删除"
    elif flow_hits:
        blocked = True
        reason = f"该表被流程引用（{'、'.join(flow_hits)}），请先从流程中移除相关步骤"

    return {
        "table": table_name, "biz_name": models[table_name].biz_name or table_name,
        "row_count": row_count,
        "referenced_by": referenced_by,
        "pages": [{"id": p.id, "title": p.title, "kind": p.kind} for p in target_pages],
        "menus": [{"id": m.id, "label": m.label} for m in menus],
        "grants": grants,
        "flows": flow_hits,
        "reports": reports,
        "seeds": seeds,
        "blocked": blocked,
        "reason": reason,
    }


def drop_table(app, deployment_id: int, table_name: str, actor_user_id: int | None) -> dict:
    """删表确认执行：守卫 → 级联清元数据 → 物理 DROP → structure 日志。"""
    info = analyze_drop_table(app, deployment_id, table_name)
    if info["blocked"]:
        raise StructureError(info["reason"])

    deployment = _load_deployment(app, deployment_id)
    models, id_map = _existing_models(app, deployment_id)
    target_id = id_map[table_name]
    removed = {"pages": 0, "menus": 0, "grants": 0, "configs": 0, "seeds": 0}

    with _session(app) as s:
        pages = s.query(PageSpec).filter_by(deployment_id=deployment_id).all()
        target_page_ids = [p.id for p in pages if p.table_id == target_id]
        menus = (
            s.query(DeploymentMenu)
            .filter(DeploymentMenu.deployment_id == deployment_id,
                    DeploymentMenu.ref_id.in_(target_page_ids or [-1]))
            .all()
        )
        menu_ids = [m.id for m in menus]
        if menu_ids:
            removed["grants"] = (
                s.query(DeploymentMenuGrant)
                .filter(DeploymentMenuGrant.menu_id.in_(menu_ids))
                .delete(synchronize_session=False)
            )
        for m in menus:
            s.delete(m)
        removed["menus"] = len(menus)
        for p in pages:
            if p.table_id == target_id:
                s.delete(p)
        removed["pages"] = len(target_page_ids)
        removed["configs"] = (
            s.query(CrudConfig).filter_by(deployment_id=deployment_id, table_id=target_id)
            .delete(synchronize_session=False)
        )
        removed["seeds"] = (
            s.query(DeploymentSeedRow)
            .filter_by(deployment_id=deployment_id, table_name=table_name)
            .delete(synchronize_session=False)
        )
        s.query(Attachment).filter_by(deployment_id=deployment_id, table_name=table_name).delete(
            synchronize_session=False
        )
        ts = s.get(TableSpec, target_id)
        if ts is not None:
            s.delete(ts)
        s.commit()

    # 物理 DROP（元数据已清；失败仅留孤儿表，可再删/重建，不产生悬垂元数据）
    engine, dialect = _biz_engine(app, deployment)
    try:
        if dialect == "mysql":
            q = f"`{table_name}`"
        elif dialect == "postgresql":
            q = f'"{table_name}"'
        else:
            q = table_name
        with engine.begin() as conn:
            conn.execute(text(f"DROP TABLE {q}"))
    finally:
        engine.dispose()

    log_system(
        app, deployment_id, "structure", actor_user_id=actor_user_id,
        object_kind="table", object_table=table_name, object_pk="",
        object_label=f"删除表：{info['biz_name']}（{info['row_count']} 行，"
                     f"{removed['pages']} 个页面）",
        changes={"action": "drop_table", "table": table_name, "cascade": removed},
    )
    return {"dropped": True, "table": table_name, "cascade": removed}
