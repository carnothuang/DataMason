"""骨架中间规格（M6 混合建库，0.12.0 / DR-35）。

混合模式第一段：LLM 只产「表集合 + 表间关系」（不产字段），Python 确定性映射为
外键列占位；第二段再逐表生成字段。本模块提供：

- `RelationSpec` / `SkeletonTable` / `SchemaSkeleton`：骨架强校验
  （表名合法且唯一、关系目标必须存在、禁自环、非空表集合）。
- `map_relations_to_columns`：relations → 外键列占位（`<目标表>_id`，
  冲突加 `related_` 语义前缀），并注入骨架主键 `id`。
- `merge_skeleton_columns`：骨架 + 各表字段 → 完整 `SchemaSpec`；
  失败返回定位到表/字段的可读错误清单（DR-38 单一数据源兜底）。
"""
from __future__ import annotations

import re

from pydantic import BaseModel, Field, ValidationError, model_validator

from datamason.ddl.ddl_builder import inject_ownership_columns
from datamason.ddl.table_spec import ColumnSpec, SchemaSpec, TableSpecModel

_NAME_PATTERN = r"^[a-zA-Z_][a-zA-Z0-9_]*$"


class RelationSpec(BaseModel):
    """表间引用关系：本表 → 目标表（被引用表）。"""

    to_table: str = Field(min_length=1, max_length=64, pattern=_NAME_PATTERN)
    note: str = ""  # 关系语义（如"订单属于客户"），仅展示不参与 DDL


class SkeletonTable(BaseModel):
    """骨架表：只有名字/业务名/关系，字段留待字段级阶段。"""

    name: str = Field(min_length=1, max_length=64, pattern=_NAME_PATTERN)
    biz_name: str = ""
    biz_notes: str = ""
    relations: list[RelationSpec] = Field(default_factory=list)


class SchemaSkeleton(BaseModel):
    """一次混合建库的表级骨架（Step 2 产物）。"""

    biz_notes: str = ""
    tables: list[SkeletonTable] = Field(min_length=1)

    @property
    def table_map(self) -> dict[str, SkeletonTable]:
        return {t.name: t for t in self.tables}

    @property
    def table_names(self) -> list[str]:
        return [t.name for t in self.tables]

    @model_validator(mode="after")
    def _validate_structure(self):
        names = [t.name for t in self.tables]
        if len(names) != len(set(names)):
            raise ValueError("存在重名表")
        nameset = set(names)
        for table in self.tables:
            for rel in table.relations:
                if rel.to_table == table.name:
                    raise ValueError(f"表 {table.name} 存在自环关系（不能引用自身）")
                if rel.to_table not in nameset:
                    raise ValueError(
                        f"表 {table.name} 的关系目标 {rel.to_table} 不存在")
        return self


def skeleton_pk_column() -> ColumnSpec:
    """骨架统一主键：id INTEGER 自增（字段级/合并时系统补齐，LLM 不产）。"""
    return ColumnSpec(
        name="id", biz_name="编号", type="INTEGER", nullable=False,
        is_pk=True, autoincrement=True,
    )


def _unique_fk_name(base: str, used: set[str]) -> str:
    """外键列名去重：`<目标表>_id` 冲突时加 `related_` / `relatedN_` 前缀。"""
    if base not in used:
        return base
    candidate = f"related_{base}"
    n = 2
    while candidate in used:
        candidate = f"related{n}_{base}"
        n += 1
    return candidate


def map_relations_to_columns(skeleton: SchemaSkeleton) -> dict[str, list[ColumnSpec]]:
    """确定性映射：每表 = 骨架主键 id + 各 relation 的外键列占位。

    外键列默认名 `<目标表名>_id`（INTEGER，指向目标表主键 id）；同名冲突时
    追加 `related_` 语义前缀，保证映射后无重名列。
    """
    tmap = skeleton.table_map
    out: dict[str, list[ColumnSpec]] = {}
    for table in skeleton.tables:
        used: set[str] = {"id"}
        columns: list[ColumnSpec] = [skeleton_pk_column()]
        for rel in table.relations:
            target = tmap.get(rel.to_table)
            target_biz = (target.biz_name if target and target.biz_name
                          else rel.to_table)
            name = _unique_fk_name(f"{rel.to_table}_id", used)
            used.add(name)
            columns.append(ColumnSpec(
                name=name, biz_name=f"{target_biz}编号", type="INTEGER",
                nullable=True, is_fk=True,
                ref_table=rel.to_table, ref_column="id",
            ))
        out[table.name] = columns
    return out


class SkeletonMergeError(ValueError):
    """合并失败：携带定位到表/字段的可读错误清单。"""

    def __init__(self, errors: list[dict]):
        self.errors = errors
        detail = "；".join(e.get("message", "") for e in errors) or "骨架合并失败"
        super().__init__(detail)


def _first_message(exc: ValidationError) -> str:
    err = exc.errors()[0]
    loc = ".".join(str(p) for p in err["loc"])
    return f"{loc}: {err['msg']}" if loc else str(err["msg"])


_LOC_TABLE = re.compile(r"表 ([a-zA-Z_][a-zA-Z0-9_]*)")
_LOC_TABLE_COL = re.compile(r"表 ([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_]*)")
_LOC_FK_COL = re.compile(r"外键 ([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_]*)")


def _locate(message: str) -> tuple[str | None, str | None]:
    """从 SchemaSpec 校验消息反解 (表, 列) 定位，供前端回到画布对应节点。"""
    m = _LOC_TABLE_COL.search(message)
    if m:
        return m.group(1), m.group(2)
    m = _LOC_FK_COL.search(message)
    if m:
        return m.group(1), m.group(2)
    m = _LOC_TABLE.search(message)
    if m:
        return m.group(1), None
    return None, None


def _ensure_pk(columns: list[ColumnSpec]) -> list[ColumnSpec]:
    """无主键时补齐骨架 id 主键（DR-05 / 版本规划 §3.3）。"""
    if any(c.is_pk for c in columns):
        return columns
    return [skeleton_pk_column(), *columns]


def merge_skeleton_columns(
    skeleton: SchemaSkeleton,
    columns_map: dict[str, list],
) -> SchemaSpec:
    """骨架 + 各表字段 → 完整 SchemaSpec（再过 SchemaSpec 全校验）。

    - 每张骨架表必须有字段（未引导字段的表进入落库即报错）；
    - 主键 id 与归属列由系统补齐（DR-05）；
    - 每条骨架关系必须有对应外键列（DR-38 单一数据源一致性）；
    - 任一步失败抛 `SkeletonMergeError`，`errors` 为 {table, column, message} 清单。
    """
    errors: list[dict] = []
    tables: list[TableSpecModel] = []

    for st in skeleton.tables:
        raw_cols = columns_map.get(st.name)
        if not raw_cols:
            errors.append({
                "table": st.name, "column": None,
                "message": f"表 {st.name} 还没有字段，请先引导字段或删除该表",
            })
            continue

        columns: list[ColumnSpec] = []
        bad = False
        for item in raw_cols:
            if isinstance(item, ColumnSpec):
                columns.append(item)
                continue
            try:
                columns.append(ColumnSpec.model_validate(item))
            except ValidationError as exc:
                name = item.get("name") if isinstance(item, dict) else None
                errors.append({
                    "table": st.name, "column": name,
                    "message": f"表 {st.name} 列 {name}: {_first_message(exc)}",
                })
                bad = True
        if bad:
            continue

        # 骨架关系 ↔ 外键列一致性（DR-38：画线=有外键列）
        for rel in st.relations:
            if not any(c.is_fk and c.ref_table == rel.to_table for c in columns):
                errors.append({
                    "table": st.name, "column": None,
                    "message": f"表 {st.name} 的关系「→ {rel.to_table}」缺少对应的外键列，"
                               "请重画连线或删除该关系",
                })
                bad = True
        if bad:
            continue

        columns = _ensure_pk(columns)
        try:
            table = TableSpecModel(
                name=st.name, biz_name=st.biz_name, biz_notes=st.biz_notes,
                columns=columns,
            )
        except ValidationError as exc:
            errors.append({
                "table": st.name, "column": None,
                "message": f"表 {st.name}: {_first_message(exc)}",
            })
            continue
        tables.append(inject_ownership_columns(table))

    if errors:
        raise SkeletonMergeError(errors)

    try:
        return SchemaSpec(tables=tables, biz_notes=skeleton.biz_notes)
    except ValidationError as exc:
        for err in exc.errors():
            message = str(err["msg"])
            table, column = _locate(message)
            errors.append({"table": table, "column": column, "message": message})
        raise SkeletonMergeError(errors) from exc
