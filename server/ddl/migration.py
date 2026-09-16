"""单表字段编辑（M1-4，消化走查 B3）：列蓝图操作 → 方言化 ALTER DDL。

方法论延续「规格驱动」：编辑对象是列蓝图（ColumnSpec 列表），本模块确定性地
把一组列操作翻译成物理 DDL，不依赖 LLM。

支持操作：
  add         新增列（强制可空；SQLite ADD COLUMN 的硬性限制）
  drop        删除列
  rename      列改名
  change_type 改逻辑类型（可选 VARCHAR 长度）

保护（v0 一律拒绝，返回 MigrationError）：
- 主键列、系统归属列（DR-05）禁止删除/改名/改类型
- 外键列、被其他表外键引用的列禁止删除/改名（防悬空引用）

方言：
- MySQL / PostgreSQL：直接 ALTER TABLE ADD/RENAME/DROP/MODIFY|ALTER COLUMN TYPE
- SQLite：不支持 DROP/RENAME 的老语法，统一用「12 步重建表」承载 drop/rename/add/change
"""
from __future__ import annotations

from copy import deepcopy

from server.ddl.ddl_builder import (
    OWNERSHIP_COLUMNS,
    SUPPORTED,
    _map_type,
    _q,
    inject_ownership_columns,
)
from server.ddl.table_spec import ColumnSpec, TableSpecModel

LOGICAL_TYPES = ("INTEGER", "BIGINT", "VARCHAR", "TEXT", "BOOLEAN", "DATETIME", "DECIMAL")


class MigrationError(ValueError):
    pass


# ---------------- 操作校验（先在内存里把整组操作跑完，再生成 DDL） ----------------

def _find(cols: list[ColumnSpec], name: str) -> ColumnSpec | None:
    return next((c for c in cols if c.name == name), None)


def _protected_reason(col: ColumnSpec, ref_targets: set[str]) -> str | None:
    if col.is_pk:
        return "主键列受保护，不能删除/改名/改类型"
    if col.is_ownership or col.name in OWNERSHIP_COLUMNS:
        return f"系统归属列 {col.name} 受保护，不能删除/改名/改类型"
    if col.is_fk:
        return f"外键列 {col.name} 受保护，v0 请删除/重建关联，不能直接改"
    if col.name in ref_targets:
        return f"列 {col.name} 被其他表外键引用，不能删除/改名"
    return None


def _apply_op_to_blueprint(cols: list[ColumnSpec], op: dict,
                           ref_targets: set[str]) -> list[ColumnSpec]:
    action = op.get("action")
    cols = [c.model_copy(deep=True) for c in cols]

    if action == "add":
        name = (op.get("name") or "").strip()
        ctype = op.get("type")
        if not name:
            raise MigrationError("新增列缺少 name")
        if ctype not in LOGICAL_TYPES:
            raise MigrationError(f"新增列类型非法: {ctype}")
        if _find(cols, name):
            raise MigrationError(f"列 {name} 已存在")
        try:
            col = ColumnSpec(
                name=name,
                biz_name=(op.get("biz_name") or "").strip(),
                type=ctype,
                length=int(op["length"]) if op.get("length") else None,
                nullable=True,  # v0：新增列强制可空
            )
        except Exception as exc:  # pydantic 校验失败
            raise MigrationError(str(exc)) from exc
        cols.append(col)
        return cols

    if action == "drop":
        name = op.get("name")
        col = _find(cols, name)
        if col is None:
            raise MigrationError(f"列 {name} 不存在")
        reason = _protected_reason(col, ref_targets)
        if reason:
            raise MigrationError(reason)
        return [c for c in cols if c.name != name]

    if action == "rename":
        old, new = op.get("name"), (op.get("new_name") or "").strip()
        col = _find(cols, old)
        if col is None:
            raise MigrationError(f"列 {old} 不存在")
        if not new:
            raise MigrationError("缺少新列名 new_name")
        if _find(cols, new):
            raise MigrationError(f"列名 {new} 已被占用")
        reason = _protected_reason(col, ref_targets)
        if reason:
            raise MigrationError(reason)
        try:
            col.name = new  # 触发 pydantic pattern 校验
        except Exception as exc:
            raise MigrationError(str(exc)) from exc
        return cols

    if action == "change_type":
        name, ctype = op.get("name"), op.get("type")
        col = _find(cols, name)
        if col is None:
            raise MigrationError(f"列 {name} 不存在")
        if ctype not in LOGICAL_TYPES:
            raise MigrationError(f"目标类型非法: {ctype}")
        if col.is_pk or col.is_ownership or col.name in OWNERSHIP_COLUMNS or col.is_fk:
            reason = _protected_reason(col, ref_targets) or "该列受保护，不能改类型"
            raise MigrationError(reason)
        col.type = ctype
        if op.get("length"):
            col.length = int(op["length"])
        elif ctype != "VARCHAR":
            col.length = None
        return cols

    raise MigrationError(f"未知操作: {action}")


def simulate(columns: list[ColumnSpec], ops: list[dict],
             all_tables: list[TableSpecModel], table_name: str) -> list[ColumnSpec]:
    """在内存中依次应用操作，返回最终列蓝图；任一步非法即抛错（不动数据库）。"""
    # 收集「被其他表外键引用」的本表列名
    ref_targets: set[str] = set()
    for t in all_tables:
        if t.name == table_name:
            continue
        for c in t.columns:
            if c.is_fk and c.ref_table == table_name and c.ref_column:
                ref_targets.add(c.ref_column)

    cols = [c.model_copy(deep=True) for c in columns]
    for op in ops:
        cols = _apply_op_to_blueprint(cols, op, ref_targets)
    if not [c for c in cols if c.is_pk]:
        raise MigrationError("改动后表将没有主键列，已拒绝")
    return cols


# ---------------- DDL 生成 ----------------

def _add_col_ddl(col: ColumnSpec, dialect: str) -> str:
    """ADD COLUMN 的列定义：不带内联 PRIMARY KEY/AUTOINCREMENT。"""
    parts = [_map_type(col, dialect)]
    parts.append("NOT NULL" if not col.nullable else "NULL")
    return " ".join(parts)


def _full_col_def(col: ColumnSpec, dialect: str) -> str:
    """CHANGE/MODIFY 用的完整列定义（含 NOT NULL；不含主键子句）。"""
    parts = [_q(col.name, dialect), _map_type(col, dialect)]
    if not col.nullable:
        parts.append("NOT NULL")
    return " ".join(parts)


def _index_ddls(table: TableSpecModel, cols: list[ColumnSpec], dialect: str) -> list[str]:
    names = {c.name for c in cols}
    stmts = []
    for i, idx_cols in enumerate(table.indexes, start=1):
        if all(c in names for c in idx_cols):
            joined = ", ".join(_q(c, dialect) for c in idx_cols)
            stmts.append(
                f"CREATE INDEX {_q(f'idx_{table.name}_{i}', dialect)} "
                f"ON {_q(table.name, dialect)} ({joined})"
            )
    return stmts


def _sqlite_rebuild(table: TableSpecModel, final_cols: list[ColumnSpec],
                   original_cols: list[ColumnSpec], ops: list[dict]) -> list[str]:
    """12 步重建表：承载任意列增删改/改名（SQLite 通用）。"""
    t = _q(table.name, "sqlite")
    tmp = f"{table.name}__mig_tmp"
    tq = _q(tmp, "sqlite")

    # 旧名 -> 新名（rename）；被 drop 的列不进入拷贝
    rename_map = {o["name"]: o["new_name"] for o in ops if o["action"] == "rename"}
    dropped = {o["name"] for o in ops if o["action"] == "drop"}
    # 保留列：最终列中能在旧表找到来源的（改名列用旧名定位）
    final_names = [c.name for c in final_cols]
    pairs: list[tuple[str, str]] = []  # (旧列名, 新列名)
    for new_name in final_names:
        old_name = next((o for o, n in rename_map.items() if n == new_name), new_name)
        if old_name in {c.name for c in original_cols} and old_name not in dropped:
            pairs.append((old_name, new_name))

    new_table = TableSpecModel(
        name=tmp, biz_name=table.biz_name,
        columns=final_cols, indexes=[], biz_notes=table.biz_notes,
    )
    from server.ddl.ddl_builder import build_table_ddl

    stmts = ["PRAGMA foreign_keys=OFF"]
    stmts.append(build_table_ddl(new_table, "sqlite", with_ownership=False))
    if pairs:
        new_list = ", ".join(_q(n, "sqlite") for _o, n in pairs)
        select_list = ", ".join(
            (_q(o, "sqlite") if o == n else f"{_q(o, 'sqlite')} AS {_q(n, 'sqlite')}")
            for o, n in pairs
        )
        stmts.append(f"INSERT INTO {tq} ({new_list}) SELECT {select_list} FROM {t}")
    stmts.append(f"DROP TABLE {t}")
    stmts.append(f"ALTER TABLE {tq} RENAME TO {t}")
    stmts.extend(_index_ddls(table, final_cols, "sqlite"))
    stmts.append("PRAGMA foreign_keys=ON")
    return stmts


def plan_sqlite(table: TableSpecModel, original_cols: list[ColumnSpec],
                final_cols: list[ColumnSpec], ops: list[dict]) -> list[str]:
    # 仅 add 且无 drop/rename/change 时可走轻量 ADD COLUMN；其余一律重建（稳妥通用）
    heavy = any(o["action"] in ("drop", "rename", "change_type") for o in ops)
    if not heavy:
        stmts = []
        for o in ops:
            if o["action"] == "add":
                col = next(c for c in final_cols if c.name == o["name"])
                stmts.append(
                    f"ALTER TABLE {_q(table.name, 'sqlite')} "
                    f"ADD COLUMN {_q(col.name, 'sqlite')} {_add_col_ddl(col, 'sqlite')}"
                )
        return stmts
    return _sqlite_rebuild(table, final_cols, original_cols, ops)


def plan_server_sql(table: TableSpecModel, original_cols: list[ColumnSpec],
                    final_cols: list[ColumnSpec], ops: list[dict],
                    dialect: str) -> list[str]:
    """MySQL / PostgreSQL 的原地 ALTER。"""
    t = _q(table.name, dialect)
    clauses: list[str] = []
    by_name = {c.name: c for c in final_cols}

    # 改名后的新列名集合：CHANGE 已用最终列定义承载类型，抑制同批对该列的冗余 MODIFY
    renamed_to = {o["new_name"] for o in ops if o["action"] == "rename"}

    for o in ops:
        act = o["action"]
        if act == "add":
            col = by_name[o["name"]]
            clauses.append(f"ADD COLUMN {_q(col.name, dialect)} {_add_col_ddl(col, dialect)}")
        elif act == "drop":
            clauses.append(f"DROP COLUMN {_q(o['name'], dialect)}")
        elif act == "rename":
            old, new = o["name"], o["new_name"]
            if dialect == "mysql":
                # CHANGE 必须给列定义；用最终定义（与同批后续改类型合并）
                col = by_name[new]
                clauses.append(
                    f"CHANGE COLUMN {_q(old, dialect)} {_full_col_def(col, dialect)}"
                )
            else:
                clauses.append(
                    f"RENAME COLUMN {_q(old, dialect)} TO {_q(new, dialect)}"
                )
        elif act == "change_type":
            name = o["name"]  # 后端按顺序处理：改名后这里已是新名
            col = by_name[name]
            if dialect == "mysql":
                if name in renamed_to:
                    continue  # 已由同批 CHANGE 的最终列定义承载
                clauses.append(f"MODIFY COLUMN {_full_col_def(col, dialect)}")
            else:
                target_type = _map_type(col, dialect)
                clauses.append(
                    f"ALTER COLUMN {_q(name, dialect)} TYPE {target_type} "
                    f"USING {_q(name, dialect)}::{target_type.split('(')[0]}"
                )
    return [f"ALTER TABLE {t} " + ", ".join(clauses)] if clauses else []


def build_migration(table: TableSpecModel, ops: list[dict],
                    all_tables: list[TableSpecModel], dialect: str
                    ) -> tuple[list[ColumnSpec], list[str]]:
    """入口：返回 (最终列蓝图, 有序 DDL 语句)。"""
    if dialect not in SUPPORTED:
        raise MigrationError(f"不支持的方言: {dialect}")
    original = [c.model_copy(deep=True) for c in table.columns]
    final = simulate(original, ops, all_tables, table.name)
    if dialect == "sqlite":
        stmts = plan_sqlite(table, original, final, ops)
    else:
        stmts = plan_server_sql(table, original, final, ops, dialect)
    return final, stmts
