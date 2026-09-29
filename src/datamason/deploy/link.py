"""直连模式（M2-7，DR-09/DR-18）：选已有连接 → 反向生成 table_spec → 生成 CRUD 配置。

数据流（设计细化 §十一·六 两种数据模式）：
  inventorize  连接 → 全库表清单（含单主键可用性 / 归属列 / FK 引用链）
  select_recommend  意图一句话 → _select_relevant_tables 两段式选表（schema 过大时）
  reverse_spec  选定表 → 反射真实 schema → SchemaSpec（类型白名单映射，自动）
  preview       反向 spec + （可选）AI 补业务标签 → 提案（复用创建模式提案 UI）

契约与红线：
- 只读外部库：全程仅 inspect/select，绝不执行 DDL/ALTER（DR-05）；
- 缺归属列：不注入、不补列，「我的记录」降级明示（工作台层消费 is_ownership）；
- 单主键：MVP CRUD 仅单主键表可用（多列/无主键标记为不可用，不进提案）。
"""
from __future__ import annotations

import re

from sqlalchemy import inspect

from datamason.ddl.table_spec import ColumnSpec, SchemaSpec, TableSpecModel
from datamason.settings.service import get_connection_engine_factory

# 归属列（DR-05 同 crud.engine 约定）
_OWNERSHIP_COLS = {"created_by", "created_at", "updated_by", "updated_at"}


class LinkError(RuntimeError):
    pass


# ---------------- 全库清单 ----------------

def inventorize(app, connection_id: int) -> list[dict]:
    """读取连接全库表清单（列名/单主键/归属列/FK 引用链）。"""
    engine = get_connection_engine_factory(app)(connection_id)
    try:
        insp = inspect(engine)
        result = []
        for table_name in insp.get_table_names():
            cols = insp.get_columns(table_name)
            pk = (insp.get_pk_constraint(table_name).get("constrained_columns") or [])
            fk_targets = sorted({
                fk["referred_table"]
                for fk in insp.get_foreign_keys(table_name)
            })
            column_names = [c["name"] for c in cols]
            ownership = sorted(_OWNERSHIP_COLS & set(column_names))
            result.append({
                "name": table_name,
                "columns": column_names,
                "column_count": len(column_names),
                "pk": pk[0] if len(pk) == 1 else None,   # 仅单主键可 CRUD
                "single_pk": len(pk) == 1,
                "has_created_by": "created_by" in column_names,
                "ownership_columns": ownership,
                "fk_targets": fk_targets,
            })
        return result
    finally:
        engine.dispose()


# ---------------- 两段式选表（能力契约 _select_relevant_tables） ----------------

def select_recommend(app, connection_id: int, purpose: str) -> dict:
    """意图一句话 → 相关表推荐；表数超阈值即启用两段式选表。"""
    from datamason.dataaccess.select_tables import (
        SELECT_MAX_TABLES,
        SELECT_TRIGGER,
        TableInventory,
        select_relevant_tables,
    )

    inventory = inventorize(app, connection_id)
    cond = [
        TableInventory(name=t["name"], columns=tuple(t["columns"]),
                       fk_targets=tuple(t["fk_targets"]))
        for t in inventory
    ]
    selection = select_relevant_tables(cond, purpose, max_tables=SELECT_MAX_TABLES)
    return {
        "triggered": len(inventory) > SELECT_TRIGGER,
        "trigger_threshold": SELECT_TRIGGER,
        "max_tables": SELECT_MAX_TABLES,
        "total": len(inventory),
        "selection": selection,
        "no_pk": [t["name"] for t in inventory if not t["single_pk"]],
    }


# ---------------- 反向生成 spec ----------------

def reverse_spec(app, connection_id: int, table_names: list[str]) -> tuple[SchemaSpec, dict]:
    """选定表 → 反射真实 schema → SchemaSpec。

    返回 (spec, notes)；notes 含不可用表/被丢弃的外键（引用选定集外或非主键）。
    不可用表（无/多主键）在选定集内主动剔除并记录；全不可用抛 LinkError。
    """
    engine = get_connection_engine_factory(app)(connection_id)
    try:
        insp = inspect(engine)
        tables_found = set(insp.get_table_names())
        missing = [t for t in table_names if t not in tables_found]
        table_names = [t for t in table_names if t in tables_found]

        # 1. 收集基础反射信息（列/主键/唯一/FK）
        raw: dict[str, dict] = {}
        for table_name in table_names:
            cols = insp.get_columns(table_name)
            pk = insp.get_pk_constraint(table_name).get("constrained_columns") or []
            unique = _unique_columns(insp, table_name)
            fk_map = _fk_map(insp, table_name)
            raw[table_name] = {"cols": cols, "pk": pk, "unique": unique, "fk": fk_map}

        usable = [t for t in table_names if len(raw[t]["pk"]) == 1]
        unusable = [t for t in table_names if t not in usable]

        if not usable:
            raise LinkError(
                f"所选表均无法用于 CRUD（缺单主键）：{', '.join(missing + table_names)}"
            )

        pk_tables = {t: raw[t]["pk"][0] for t in usable}
        dropped_fks: list[dict] = []
        model_tables: list[TableSpecModel] = []
        for table_name in usable:
            info = raw[table_name]
            pk_col = info["pk"][0]
            columns: list[ColumnSpec] = []
            for col in info["cols"]:
                name = col["name"]
                db_type = _db_type_text(col)
                logical, length = _logical_type(col, db_type)
                is_fk, ref_table, ref_column = False, None, None
                maybe_fk = info["fk"].get(name)
                if maybe_fk is not None:
                    target, target_col = maybe_fk
                    if target in pk_tables and pk_tables[target] == target_col:
                        is_fk, ref_table, ref_column = True, target, target_col
                    else:
                        dropped_fks.append({
                            "table": table_name, "column": name,
                            "ref_table": target, "ref_column": target_col,
                            "reason": "引用目标不在本次选定集或未指向外键方主键",
                        })
                columns.append(ColumnSpec(
                    name=name,
                    biz_name="",
                    type=logical,
                    length=length,
                    nullable=bool(col.get("nullable", True)),
                    is_pk=name == pk_col,
                    autoincrement=name == pk_col and "INT" in db_type,
                    is_fk=is_fk,
                    ref_table=ref_table,
                    ref_column=ref_column,
                    unique=name in info["unique"],
                    is_ownership=name in _OWNERSHIP_COLS,
                ))
            model_tables.append(TableSpecModel(
                name=table_name,
                biz_name="",
                columns=columns,
            ))

        from pydantic import ValidationError

        try:
            schema = SchemaSpec(tables=model_tables, biz_notes="")
        except ValidationError as exc:
            msgs = []
            for err in exc.errors():
                loc = ".".join(str(p) for p in err["loc"])
                msgs.append(f"{loc}: {err['msg']}")
            raise LinkError(f"外部库结构无法进入 DataMason 规格：{'；'.join(msgs[:5])}") from exc
        notes = {
            "missing_tables": missing,
            "unusable_tables": unusable,
            "dropped_fks": dropped_fks,
        }
        return schema, notes
    finally:
        engine.dispose()


# ---------------- 直连预览（提案，不落库） ----------------

def preview(app, connection_id: int, table_names: list[str],
            purpose: str = "", enhance: bool = False) -> dict:
    """反向 spec → （可选）AI 补业务标签 → 提案（复用创建模式提案形态）。"""
    from datamason.deploy import service as deploy_service
    from datamason.llm.structure import enrich_labels
    from datamason.settings.service import get_default_llm_client

    names = [n.strip() for n in (table_names or []) if n and n.strip()]
    if not names:
        raise LinkError("请至少选择一张表")

    deduped = list(dict.fromkeys(names))  # 去重且保序
    schema, notes = reverse_spec(app, connection_id, deduped)

    attempts = 1
    enrich_failed = ""
    if enhance:
        from datamason.ai import service as ai_service

        client = get_default_llm_client(app)
        if client is None:
            enrich_failed = "尚未配置默认 LLM 模型，业务标签留待后续补充"
            ai_service.record(app, "crud", "error", prompt_summary=purpose or "直连补标签",
                              result_summary=enrich_failed)
        else:
            try:
                schema, enrich_meta = enrich_labels(client, purpose or "", schema.model_dump(mode="json"))
                ai_service.record(
                    app, "crud", "ok", prompt_summary=purpose or "直连补标签",
                    result_summary=f"补业务标签 {len(schema.tables)} 表（直连模式）",
                    usage=enrich_meta.get("usage"))
            except Exception as exc:  # noqa: BLE001 补标签为可选增强，失败回退确定性默认
                enrich_failed = f"{type(exc).__name__}: {exc}"
                ai_service.record(app, "crud", "error",
                                  prompt_summary=purpose or "直连补标签",
                                  result_summary=enrich_failed)

    from datamason.deploy.service import _derive_pages

    return {
        "data_mode": "link",
        "connection_id": connection_id,
        "biz_notes": schema.biz_notes or purpose or "直连已有数据库",
        "attempts": attempts,
        "spec_json": schema.model_dump(mode="json"),
        "tables": [
            {
                "name": t.name,
                "biz_name": t.biz_name or t.name,
                "columns": [
                    {"name": c.name, "biz_name": c.biz_name, "type": c.type,
                     "is_pk": c.is_pk, "is_fk": c.is_fk,
                     "ref_table": c.ref_table}
                    for c in t.columns
                ],
            }
            for t in schema.tables
        ],
        # 直连模式只派生成员 CRUD 列表页（对外部库不开放公开写表单，MVP 收敛）
        "pages": _derive_pages(schema, with_public_forms=False),
        "link": {
            "selected_tables": [t.name for t in schema.tables],
            "no_pk_tables": notes["unusable_tables"] + notes["missing_tables"],
            "missing_tables": notes["missing_tables"],
            "dropped_fks": notes["dropped_fks"],
            "ownership_missing": [
                t.name for t in schema.tables
                if not any(c.name == "created_by" for c in t.columns)
            ],
            "enrich_failed": enrich_failed,
        },
    }


# ---------------- 反射辅助 ----------------

def _db_type_text(col: dict) -> str:
    """列的真实类型字符串（大写，去参数描述形式如 VARCHAR(255)）。"""
    t = col["type"]
    raw = str(t)
    return re.sub(r"\(.*\)$", "", raw).upper() if re.match(r"^\w+\(", raw) else raw.upper()


def _logical_type(col: dict, db_type: str) -> tuple[str, int | None]:
    """真实 db 类型 → 白名单逻辑类型（挨着展厅对齐 table_spec.ColumnType）。"""
    length_attr = getattr(col["type"], "length", None)
    length = int(length_attr) if length_attr else None
    base = db_type.split()[0]
    if base in ("INTEGER", "INT", "MEDIUMINT", "SMALLINT", "TINYINT"):
        return ("INTEGER", length)
    if base in ("BIGINT", "BIGSERIAL", "BIG INTEGER"):
        return ("BIGINT", length)
    if base in ("VARCHAR", "CHAR", "NVARCHAR", "NCHAR"):
        return ("VARCHAR", length or 255)
    if base in ("TEXT", "CLOB", "LONGTEXT", "MEDIUMTEXT", "LONGBLOB", "BLOB", "BYTEA"):
        return ("TEXT", None)
    if base in ("BOOLEAN", "BOOL"):
        return ("BOOLEAN", None)
    if base in ("DATETIME", "TIMESTAMP", "DATE", "TIME"):
        return ("DATETIME", None)
    if base in ("DECIMAL", "NUMERIC", "FLOAT", "DOUBLE", "REAL", "MONEY"):
        return ("DECIMAL", None)
    return ("TEXT", None)


def _unique_columns(insp, table_name: str) -> set[str]:
    """单列唯一：唯一约束 + 单列唯一索引（对齐 crud.engine._unique_columns）。"""
    uniq: set[str] = set()
    for cons in insp.get_unique_constraints(table_name):
        for c in cons.get("column_names") or []:
            uniq.add(c)
    for idx in insp.get_indexes(table_name):
        if idx.get("unique") and len(idx.get("column_names") or []) == 1:
            uniq.add(idx.get("column_names")[0])
    return uniq


def _fk_map(insp, table_name: str) -> dict[str, tuple[str, str]]:
    """表内单列外键映射 {列名: (目标表, 目标列)}；多列外键跳过（MVP 单列）。"""
    out: dict[str, tuple[str, str]] = {}
    for fk in insp.get_foreign_keys(table_name):
        cols = fk.get("constrained_columns") or []
        refs = fk.get("referred_columns") or []
        if len(cols) == 1 and len(refs) == 1:
            out[cols[0]] = (fk.get("referred_table"), refs[0])
    return out