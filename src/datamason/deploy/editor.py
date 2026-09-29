"""表编辑器 v0 服务（M1-4，消化 B3）：单表字段增删/改名/改类型。

- 蓝图来源：table_specs.columns_json（富化列：name/biz_name/type/length/nullable/
  is_pk/autoincrement/is_fk/ref_table/ref_column/is_ownership）。
- 对历史部署（只存了 name/type/nullable 的简版）做一次反射补全。
- 物理迁移由 ddl.migration 确定性生成；改完回写蓝图。反射浏览每次请求新建
  engine 重新 reflect，因此 DDL 提交后下一次浏览自动生效，无需清缓存。
"""
from __future__ import annotations

import json

from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine

from datamason.dataaccess.manager import execute_ddl
from datamason.ddl.migration import MigrationError, build_migration
from datamason.ddl.table_spec import ColumnSpec, TableSpecModel
from datamason.models import Connection, Deployment, TableSpec


class EditorError(RuntimeError):
    pass


def _session(app):
    return app.extensions["datamason_session_factory"]()


def _biz_engine(app, deployment: Deployment) -> tuple[Engine, str]:
    cipher = app.extensions["cipher"]
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        conn = s.get(Connection, deployment.connection_id)
        if conn is None:
            raise EditorError("部署没有关联数据连接")
        uri = cipher.decrypt(conn.conn_uri)
        dialect = conn.dialect
    return create_engine(uri, future=True), dialect


def _load_specs(app, deployment_id: int):
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        deployment = s.get(Deployment, deployment_id)
        if deployment is None:
            raise KeyError("部署不存在")
        rows = (s.query(TableSpec)
                .filter_by(deployment_id=deployment_id)
                .order_by(TableSpec.id).all())
        snapshot = [(r.id, r.name, r.biz_name, r.columns_json) for r in rows]
    return deployment, snapshot


def _enrich_from_db(engine: Engine, table_name: str, cols: list[dict]) -> list[dict]:
    """历史简版蓝图补全：反射主键/可空；归属列打标。外键信息缺失则不恢复。"""
    if cols and any("is_pk" in c for c in cols):
        return cols  # 已是富化版
    insp = inspect(engine)
    pk = set((insp.get_pk_constraint(table_name).get("constrained_columns") or []))
    real = {c["name"]: c for c in insp.get_columns(table_name)}
    out = []
    for c in cols:
        name = c["name"]
        rc = real.get(name, {})
        is_own = name in ("created_by", "created_at", "updated_by", "updated_at")
        out.append({
            "name": name,
            "biz_name": "",
            "type": _logical_type(str(rc.get("type", c.get("type", "VARCHAR")))),
            "length": None,
            "nullable": bool(rc.get("nullable", c.get("nullable", True))),
            "is_pk": name in pk,
            "autoincrement": name in pk,
            "is_fk": False,
            "ref_table": None,
            "ref_column": None,
            "is_ownership": is_own,
        })
    return out


def _logical_type(db_type: str) -> str:
    t = db_type.upper()
    if t.startswith("BIGINT"):
        return "BIGINT"
    if t.startswith(("INT", "TINYINT")):
        return "INTEGER"
    if t.startswith("VARCHAR"):
        return "VARCHAR"
    if t.startswith("BOOLEAN"):
        return "BOOLEAN"
    if t.startswith(("DATETIME", "TIMESTAMP")):
        return "DATETIME"
    if t.startswith("DECIMAL") or t.startswith("NUMERIC"):
        return "DECIMAL"
    if t.startswith("TEXT") or t.startswith("CLOB"):
        return "TEXT"
    return "VARCHAR"


def _to_model(name: str, biz_name: str, cols: list[dict]) -> TableSpecModel:
    return TableSpecModel(
        name=name,
        biz_name=biz_name or "",
        columns=[ColumnSpec(**c) for c in cols],
        indexes=[],
    )


def _assert_editable(deployment: Deployment) -> None:
    """直连模式 = 外部库（DR-05/DR-09）：结构受保护，绝不执行 ALTER（M2-7 红线）。"""
    if (getattr(deployment, "data_mode", "create") or "create") == "link":
        raise EditorError(
            "该部署为直连模式（外部库结构受保护，DR-05/09）。"
            "如需改结构请在创建模式重新部署，DataMason 不会对外部库执行 ALTER"
        )


def _diff_ops(ops: list[dict]) -> list[dict]:
    """把列操作列表加工成「人话 diff」供提案卡审查（before/after 都在列名+类型里）。"""
    out: list[dict] = []
    for op in ops:
        action = op.get("action")
        if action == "add":
            out.append({
                "action": "add",
                "name": op.get("name", ""),
                "biz_name": op.get("biz_name", ""),
                "type": op.get("type", ""),
                "length": op.get("length"),
            })
        elif action == "drop":
            out.append({"action": "drop", "name": op.get("name", "")})
        elif action == "rename":
            out.append({
                "action": "rename",
                "old": op.get("name", ""),
                "name": op.get("new_name", ""),
            })
        elif action == "change_type":
            out.append({
                "action": "change_type",
                "name": op.get("name", ""),
                "type": op.get("type", ""),
                "length": op.get("length"),
            })
    return out


def propose_table(app, deployment_id: int, table_name: str, ops: list[dict]) -> dict:
    """生成表结构变更的 diff 提案：只算不执行（生成即提案，确认后才 ALTER）。

    M2-8 把 M1-4 的直接 ALTER 升级为「diff 提案卡」：返回人类可读 diff +
    方言化 DDL 语句 + 变更后列蓝图；前端审查确认后调 alter_table 落地。
    """
    if not isinstance(ops, list) or not ops:
        raise EditorError("ops 必须是非空操作列表")

    deployment, snapshot = _load_specs(app, deployment_id)
    _assert_editable(deployment)
    engine, dialect = _biz_engine(app, deployment)

    models: dict[str, TableSpecModel] = {}
    try:
        for row_id, name, biz_name, raw in snapshot:
            cols = _enrich_from_db(engine, name, json.loads(raw or "[]"))
            models[name] = _to_model(name, biz_name, cols)
    finally:
        engine.dispose()

    if table_name not in models:
        raise KeyError(f"表 {table_name} 不存在")
    target = models[table_name]
    all_tables = list(models.values())

    try:
        final_cols, statements = build_migration(target, ops, all_tables, dialect)
    except MigrationError as exc:
        raise EditorError(str(exc)) from exc
    except Exception as exc:  # 含 pydantic.ValidationError（非法列名等）
        raise EditorError(f"字段变更校验失败：{exc}") from exc

    return {
        "table": table_name,
        "dirty": bool(statements),
        "diff": _diff_ops(ops),
        "statements": statements,
        "final_columns": [_column_public(c) for c in final_cols],
    }


def _column_public(c: ColumnSpec) -> dict:
    return {
        "name": c.name,
        "biz_name": c.biz_name,
        "type": c.type,
        "length": c.length,
        "nullable": c.nullable,
        "is_pk": c.is_pk,
        "is_fk": c.is_fk,
        "ref_table": c.ref_table,
        "is_ownership": c.is_ownership,
    }


def get_blueprint(app, deployment_id: int) -> list[dict]:
    """返回该部署每张表的富化列蓝图（供编辑器渲染）。"""
    deployment, snapshot = _load_specs(app, deployment_id)
    engine, _dialect = _biz_engine(app, deployment)
    try:
        result = []
        for _id, name, biz_name, raw in snapshot:
            cols = _enrich_from_db(engine, name, json.loads(raw or "[]"))
            result.append({"name": name, "biz_name": biz_name or name,
                           "columns": [_column_public(ColumnSpec(**c)) for c in cols]})
        return result
    finally:
        engine.dispose()


def alter_table(app, deployment_id: int, table_name: str, ops: list[dict]) -> dict:
    """对单表应用一组列操作（diff 提案卡的「确认执行」）：校验→物理 DDL→回写蓝图。"""
    if not isinstance(ops, list) or not ops:
        raise EditorError("ops 必须是非空操作列表")

    deployment, snapshot = _load_specs(app, deployment_id)
    _assert_editable(deployment)
    engine, dialect = _biz_engine(app, deployment)

    # 组装整库模型（跨表外键引用保护要用）
    models: dict[str, TableSpecModel] = {}
    raw_map: dict[str, list[dict]] = {}
    id_map: dict[str, int] = {}
    biz_map: dict[str, str] = {}
    try:
        for row_id, name, biz_name, raw in snapshot:
            cols = _enrich_from_db(engine, name, json.loads(raw or "[]"))
            raw_map[name] = cols
            id_map[name] = row_id
            biz_map[name] = biz_name
            models[name] = _to_model(name, biz_name, cols)
    finally:
        pass

    if table_name not in models:
        engine.dispose()
        raise KeyError(f"表 {table_name} 不存在")

    target = models[table_name]
    all_tables = list(models.values())

    try:
        final_cols, statements = build_migration(target, ops, all_tables, dialect)
    except MigrationError as exc:
        engine.dispose()
        raise EditorError(str(exc)) from exc
    except Exception as exc:  # 含 pydantic.ValidationError（非法列名等）
        engine.dispose()
        raise EditorError(f"字段变更校验失败：{exc}") from exc

    if not statements:
        engine.dispose()
        return {"name": table_name, "columns": [_column_public(c) for c in final_cols]}

    # 执行物理迁移
    try:
        execute_ddl(engine, statements)
    except Exception as exc:  # noqa: BLE001
        engine.dispose()
        raise EditorError(f"数据库执行字段变更失败：{type(exc).__name__}: {exc}") from exc
    engine.dispose()

    # 回写该表富化蓝图
    payload = [_column_public(c) for c in final_cols]
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        row = s.get(TableSpec, id_map[table_name])
        if row is not None:
            row.columns_json = json.dumps(payload, ensure_ascii=False)
            s.commit()

    return {"name": table_name, "columns": payload, "statements": statements}
