"""元数据驱动的反射 CRUD 引擎（设计细化 §十一·五）。

- SQLAlchemy Core 反射真实 schema → 动态 Table 对象（不为每张表写 model/代码）
- PostgREST 风格 REST：/tables/{name}/rows，参数化查询，列白名单
- M0-4：以 CrudRegistry 注册「engine + 表白名单」，挂 demo 蓝图前缀；
  M2-3：handler 函数化，供工作台按 /app/{deployment_id}/api 复用，
  并叠加会话用户归属列注入（current_user_id）+ refs 外键下拉；
  M2-6：写路径两阶段日志钩子（audit，DR-03）——业务写提交成功 → committed，
  失败 → 丢弃 pending；UPDATE 只存变更字段、DELETE 存可读摘要。
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Iterable

from flask import Blueprint, abort, jsonify, request
from sqlalchemy import (
    MetaData,
    Table,
    UniqueConstraint,
    asc,
    desc,
    func,
    or_,
    select,
    text,
)
from sqlalchemy.engine import Engine

# 归属列（DR-05）：系统保留，refs 下拉选择业务标签时跳过
_OWNERSHIP_COLS = {"created_by", "created_at", "updated_by", "updated_at"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CrudRegistry:
    """一个 registry 绑定一个物理库 engine 与允许暴露的表集合。

    current_user_id：归属列（created_by/updated_by，DR-05）的写入身份；
    M2-3 起由认证会话注入真实 users.id（M0 默认 1 兼容旧链路）。
    """

    def __init__(
        self,
        engine: Engine,
        tables: Iterable[str] | None = None,
        current_user_id: int = 1,
        owner_only: bool = False,
        action_map: dict[str, set[str]] | None = None,
        scope_map: dict[str, str] | None = None,
        value_validator=None,
    ):
        """owner_only（M2-5）：仅展示/操作「当前用户创建的记录」。
        操作员角色由工作台层注入 True，实现对业务数据按归属列的强制撒权过滤（DR-05）。

        0.11.0 M5-C（DR-30）：action_map = 表名→允许动作集合；scope_map = 表名→数据范围。
        均为 None 表示不限制（创建人/管理员/服务身份）。两者齐备时优先于 owner_only。

        0.13.0 M7-2：value_validator = `(table_name, values) -> None` 写前校验回调
        （表单控件配置的候选项/时间等语义校验），默认 None 表示不校验（存量行为零变化）。
        """
        self.engine = engine
        self.current_user_id = current_user_id
        self.owner_only = owner_only
        self.action_map = action_map
        self.scope_map = scope_map
        self.value_validator = value_validator
        self.meta = MetaData()
        # 空列表 = 明确「无任何表可访问」（不可退化为全量反射——操作员无授权时须 404）
        if tables is None:
            self.meta.reflect(bind=engine)
        else:
            allowed = list(tables)
            if allowed:
                self.meta.reflect(bind=engine, only=allowed)
        # only= 参数可能带 schema，表名取最后一段
        self.allowed_tables = {t.split(".")[-1] for t in self.meta.tables}
        self._reflect_fk_targets()

    def require_action(self, table_name: str, action: str) -> None:
        """动作级权限强制（0.11.0 M5-C2，DR-30）：无 action_map 时不限制。"""
        if self.action_map is None:
            return
        if action not in self.action_map.get(table_name, set()):
            abort(403, description="你没有该操作的权限")

    def owner_only_for(self, table_name: str) -> bool:
        """该表的数据范围是否为「仅自己」。scope_map 存在时按其判定，否则回退全局 owner_only。"""
        if self.scope_map is not None:
            return self.scope_map.get(table_name, "own") == "own"
        return self.owner_only

    def _reflect_fk_targets(self) -> None:
        """补反射「被外键引用但不在白名单」的目标表，保证 refs 下拉可用。"""
        seen = {k.split(".")[-1] for k in self.meta.tables}
        frontier = [k for k in seen if k in self.meta.tables]
        while frontier:
            key = frontier.pop()
            table = self.meta.tables[key]
            for column in table.columns:
                for fk in column.foreign_keys:
                    target = fk.column.table.name
                    if target in seen:
                        continue
                    try:
                        self.meta.reflect(bind=self.engine, only=[target])
                    except Exception:  # noqa: BLE001 目标表缺失/权限不足时跳过
                        continue
                    seen.add(target)
                    frontier.append(target)

    def table(self, name: str) -> Table:
        full = None
        for key in self.meta.tables:
            if key.split(".")[-1] == name:
                full = key
                break
        if full is None or name not in self.allowed_tables:
            abort(404, description=f"table not found: {name}")
        return self.meta.tables[full]


def _pk_column(table: Table):
    pks = list(table.primary_key.columns)
    if len(pks) != 1:
        abort(400, description="MVP CRUD 仅支持单主键表")
    return pks[0]


def _coerce_value(column, raw):
    """简单类型还原：bool/int/float/str + 日期时间。交由数据库做最终类型校验。"""
    tname = str(column.type).upper()
    if raw is None:
        return None
    if tname in ("DATETIME", "TIMESTAMP", "DATE"):
        if isinstance(raw, datetime):
            return raw
        txt = str(raw).strip()
        try:
            return datetime.fromisoformat(txt)
        except ValueError:
            pass
        from email.utils import parsedate_to_datetime

        parsed = parsedate_to_datetime(txt)
        if parsed is None:
            raise ValueError(f"无法识别的日期时间: {raw!r}")
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    if "INT" in tname and "BIGINT" not in tname:
        return int(raw)
    if "BOOL" in tname or tname.startswith("TINYINT(1)"):
        return str(raw).lower() in ("1", "true", "yes")
    return raw


def _filtered(table):
    """根据查询参数构造 WHERE：仅允许表内列、全部参数化。"""
    conditions = []
    reserved = {"page", "page_size", "sort", "order", "search", "cols",
                "aggregation", "column"}  # M9-6：stat 接口的聚合参数不参与列过滤
    for key, value in request.args.items():
        if key in reserved:
            continue
        if key not in table.c:
            abort(400, description=f"未知过滤列: {key}")
        conditions.append(table.c[key] == value)
    return conditions


# ---------------- handler 函数（registry 显式传入，蓝图层/工作台层复用） ----------------

def _is_string_type(column) -> bool:
    return str(column.type).upper().startswith(("VARCHAR", "CHAR", "TEXT", "STRING", "ENUM"))


def _unique_columns(engine: Engine, table: Table) -> set[str]:
    """收集单列唯一约束：UNIQUE 约束 + 单列唯一索引 + SQLite 列级 UNIQUE。

    SQLAlchemy 不反射 SQLite 的列级 UNIQUE（自动索引 origin='u'），
    需用 PRAGMA index_list 补齐；MySQL/PG 走约束/索引反射即可。
    """
    cols: set[str] = set()
    for cons in table.constraints:
        if isinstance(cons, UniqueConstraint):
            cols.update(c.name for c in cons.columns)
    for idx in table.indexes:
        if getattr(idx, "unique", False) and len(list(idx.columns)) == 1:
            cols.add(list(idx.columns)[0].name)
    if engine.dialect.name == "sqlite":
        with engine.connect() as conn:
            rows = conn.execute(
                text(f"PRAGMA index_list({_q_ident(table.name)})")
            ).fetchall()
            for row in rows:
                if row[2] != 1 or (len(row) > 4 and row[3] == "pk"):
                    continue  # 仅唯一索引；若 origin 可见则排除主键
                parts = conn.execute(
                    text(f'PRAGMA index_info("{row[1]}")')
                ).fetchall()
                if len(parts) == 1:
                    cols.add(parts[0][2])
    return cols


def _q_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def get_meta(registry: CrudRegistry) -> dict:
    out = {}
    for key, table in registry.meta.tables.items():
        uniq = _unique_columns(registry.engine, table)
        columns = []
        for c in table.columns:
            fk = None
            for f in c.foreign_keys:
                fk = {"ref_table": f.column.table.name, "ref_column": f.column.name}
                break
            columns.append({
                "name": c.name,
                "type": str(c.type),
                "length": c.type.length if hasattr(c.type, "length") else None,
                "nullable": c.nullable,
                "pk": c.primary_key,
                "unique": c.name in uniq,
                "fk": fk,
            })
        out[table.name] = {"columns": columns}
    return out


def _owner_condition(registry: "CrudRegistry", table: Table):
    """「我的记录」强制过滤（DR-05/§十一 七）：操作员只见 created_by=自己的记录。

    仅在表含归属列 created_by 且该表数据范围为「仅自己」时生效；返回 SQLAlchemy 条件或 None。
    0.11.0：范围由 registry.owner_only_for(table.name) 判定（可配置，DR-30）。
    """
    if registry.owner_only_for(table.name) and "created_by" in table.c:
        return table.c.created_by == registry.current_user_id
    return None


def get_row(registry: CrudRegistry, name: str, pk: str) -> dict:
    table = registry.table(name)
    registry.require_action(name, "view")
    pk_col = _pk_column(table)
    owner = _owner_condition(registry, table)
    cond = [pk_col == _coerce_value(pk_col, pk)]
    if owner is not None:
        cond.append(owner)
    with registry.engine.connect() as conn:
        row = conn.execute(select(table).where(*cond)).mappings().first()
    if row is None:
        abort(404)
    return dict(row)


# ---------------- M2-6 日志辅助（两阶段，DR-03） ----------------

def _jsonable(value):
    """日志展示值：datetime → 'YYYY-MM-DD HH:MM:SS'（转 naive UTC，前端可直接展示）。"""
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    return value


def _same_value(a, b) -> bool:
    """宽松相等：bool vs 0/1、int vs float、datetime 统一格式化后再比。"""
    if a is None or b is None:
        return a is b
    ja, jb = _jsonable(a), _jsonable(b)
    if isinstance(ja, bool) or isinstance(jb, bool):
        return bool(ja) == bool(jb)
    if isinstance(ja, (int, float)) and isinstance(jb, (int, float)):
        return float(ja) == float(jb)
    return ja == jb


def _row_label(registry: "CrudRegistry", table: Table, pk_value) -> str:
    """读取某行业务标签 → 日志 object_label 摘要（记录删除后仍读得懂）。"""
    pk_col = _pk_column(table)
    with registry.engine.connect() as conn:
        row = conn.execute(
            select(table).where(pk_col == pk_value)
        ).mappings().first()
    if row is None:
        return f"#{pk_value}"
    label_col = _label_column(table)
    value = row[label_col] if label_col else None
    if value is None:
        return f"#{pk_value}"
    text = str(value)
    return text[:120] + "…" if len(text) > 120 else text


def _label_from_row(table: Table, row: dict, pk_value) -> str:
    """更新/删除前用已读出的旧行生成标签，避免二次读库。"""
    label_col = _label_column(table)
    value = row[label_col] if label_col else None
    if value is None:
        return f"#{pk_value}"
    text = str(value)
    return text[:120] + "…" if len(text) > 120 else text


def _diff_changes(old_row: dict, new_values: dict) -> dict:
    """变更字段集合（M2-6）：仅含实际变化、且排除系统归属列。"""
    changes = {}
    for key, new_value in new_values.items():
        if key in _OWNERSHIP_COLS:
            continue
        old_value = old_row.get(key)
        if not _same_value(old_value, new_value):
            changes[key] = {"old": _jsonable(old_value), "new": _jsonable(new_value)}
    return changes


# ---------------- M2-3 业务写路径（M2-6：两阶段日志钩子） ----------------

def _run_value_validator(registry: "CrudRegistry", table_name: str, values: dict) -> None:
    """M7-2：写前表单控件校验（未注入回调时零行为变化）；违规 → 400 可读错误。"""
    validator = getattr(registry, "value_validator", None)
    if validator is None:
        return
    try:
        validator(table_name, values)
    except ValueError as exc:
        abort(400, description=str(exc))


def create_row(registry: CrudRegistry, name: str, audit=None) -> dict:
    table = registry.table(name)
    registry.require_action(name, "create")
    payload = request.get_json(force=True) or {}
    values = {}
    # 列白名单：不在反射 schema 里的键一律忽略；归属列（DR-05）禁止客户端改写
    for key, value in payload.items():
        if key in table.c and key not in _OWNERSHIP_COLS and not table.c[key].primary_key:
            values[key] = _coerce_value(table.c[key], value)
    # 归属列自动注入（DR-05）：业务表强制四列，一律以会话用户为准
    now = _utcnow()
    if "created_by" in table.c:
        values["created_by"] = registry.current_user_id
    if "created_at" in table.c:
        values["created_at"] = now
    if "updated_by" in table.c:
        values["updated_by"] = registry.current_user_id
    if "updated_at" in table.c:
        values["updated_at"] = now
    _run_value_validator(registry, table.name, values)
    # 日志意图（①pending）：含待写入值，供启动对账按值反查主键
    intent = {
        k: {"old": None, "new": _jsonable(v)}
        for k, v in values.items() if k not in _OWNERSHIP_COLS
    }
    log_id = audit.begin("create", table.name, changes=intent) if audit else None
    try:
        with registry.engine.begin() as conn:
            result = conn.execute(table.insert().values(**values))
            pk = result.inserted_primary_key[0]
        if audit:
            audit.commit(log_id, object_pk=str(pk), object_label=_row_label(registry, table, pk))
    except Exception:
        if audit and log_id is not None:
            audit.abort(log_id)
        raise
    return {"id": pk}


def update_row(registry: CrudRegistry, name: str, pk: str, audit=None) -> dict:
    table = registry.table(name)
    registry.require_action(name, "update")
    pk_col = _pk_column(table)
    payload = request.get_json(force=True) or {}
    values = {}
    for key, value in payload.items():
        if key in table.c and key not in _OWNERSHIP_COLS and not table.c[key].primary_key:
            values[key] = _coerce_value(table.c[key], value)
    if "updated_by" in table.c:
        values["updated_by"] = registry.current_user_id
    if "updated_at" in table.c:
        values["updated_at"] = _utcnow()
    if not values:
        abort(400, description="没有可更新的列")
    _run_value_validator(registry, table.name, values)
    cond = [pk_col == _coerce_value(pk_col, pk)]
    owner = _owner_condition(registry, table)
    if owner is not None:
        cond.append(owner)
    # ①：读旧行（含授权过滤）→ 变更字段 + 摘要标签
    with registry.engine.connect() as conn:
        old_row = conn.execute(select(table).where(*cond)).mappings().first()
    if old_row is None:
        abort(404)  # 目标不存在/不可见 → 不产生日志
    changes = _diff_changes(dict(old_row), values)
    log_id = audit.begin("update", table.name, object_pk=str(pk),
                         object_label=_label_from_row(table, dict(old_row), pk),
                         changes=changes) if audit else None
    try:
        with registry.engine.begin() as conn:
            result = conn.execute(table.update().where(*cond).values(**values))
        if result.rowcount == 0:
            abort(404)
        if audit and log_id is not None:
            audit.commit(log_id)
    except Exception:
        if audit and log_id is not None:
            audit.abort(log_id)
        raise
    return {"updated": True}


def delete_row(registry: CrudRegistry, name: str, pk: str, audit=None) -> dict:
    table = registry.table(name)
    registry.require_action(name, "delete")
    pk_col = _pk_column(table)
    cond = [pk_col == _coerce_value(pk_col, pk)]
    owner = _owner_condition(registry, table)
    if owner is not None:
        cond.append(owner)
    # ①：读旧行 → 摘要标签（记录删除后日志仍可辨认）
    with registry.engine.connect() as conn:
        old_row = conn.execute(select(table).where(*cond)).mappings().first()
    if old_row is None:
        abort(404)  # 目标不存在/不可见 → 不产生日志
    log_id = audit.begin("delete", table.name, object_pk=str(pk),
                         object_label=_label_from_row(table, dict(old_row), pk)) if audit else None
    try:
        with registry.engine.begin() as conn:
            result = conn.execute(table.delete().where(*cond))
        if result.rowcount == 0:
            abort(404)
        if audit and log_id is not None:
            audit.commit(log_id)
    except Exception:
        if audit and log_id is not None:
            audit.abort(log_id)
        raise
    return {"deleted": True}


def list_rows(registry: CrudRegistry, name: str, with_search: bool = True) -> dict:
    table = registry.table(name)
    registry.require_action(name, "view")
    page = max(int(request.args.get("page", 1)), 1)
    page_size = min(max(int(request.args.get("page_size", 20)), 1), 200)

    conditions = _filtered(table)
    owner = _owner_condition(registry, table)
    if owner is not None:
        conditions.append(owner)
    search = (request.args.get("search") or "").strip()
    if search and with_search:
        text_cols = [
            c for c in table.columns
            if _is_string_type(c) and not c.primary_key and c.name not in _OWNERSHIP_COLS
        ]
        if text_cols:
            conditions.append(
                or_(*[c.ilike(f"%{search}%") for c in text_cols])
            )

    stmt = select(table).where(*conditions)

    sort = request.args.get("sort")
    if sort:
        if sort not in table.c:
            abort(400, description=f"未知排序列: {sort}")
        direction = desc if request.args.get("order", "asc") == "desc" else asc
        stmt = stmt.order_by(direction(table.c[sort]))

    with registry.engine.connect() as conn:
        total = conn.execute(
            select(func.count()).select_from(table).where(*conditions)
        ).scalar_one()
        rows = conn.execute(
            stmt.limit(page_size).offset((page - 1) * page_size)
        ).mappings().all()

    return {
        "data": [dict(r) for r in rows],
        "page": page,
        "page_size": page_size,
        "total": total,
    }


def export_rows(registry: CrudRegistry, name: str, max_rows: int = 20000
                ) -> tuple[list[str], list[dict], bool]:
    """导出用：不分页取匹配行（上限 max_rows），复用搜索/过滤/排序/归属过滤。

    返回 (列名, 行, 是否被截断)。`cols=a,b,c` 指定导出列（D-5）。
    """
    table = registry.table(name)
    registry.require_action(name, "export")
    conditions = _filtered(table)
    owner = _owner_condition(registry, table)
    if owner is not None:
        conditions.append(owner)
    search = (request.args.get("search") or "").strip()
    if search:
        text_cols = [
            c for c in table.columns
            if _is_string_type(c) and not c.primary_key and c.name not in _OWNERSHIP_COLS
        ]
        if text_cols:
            conditions.append(or_(*[c.ilike(f"%{search}%") for c in text_cols]))
    stmt = select(table).where(*conditions)
    sort = request.args.get("sort")
    if sort:
        if sort not in table.c:
            abort(400, description=f"未知排序列: {sort}")
        direction = desc if request.args.get("order", "asc") == "desc" else asc
        stmt = stmt.order_by(direction(table.c[sort]))
    with registry.engine.connect() as conn:
        rows = conn.execute(stmt.limit(max_rows + 1)).mappings().all()
    truncated = len(rows) > max_rows
    rows = rows[:max_rows]
    all_columns = [c.name for c in table.columns]
    columns = all_columns
    cols_arg = (request.args.get("cols") or "").strip()
    if cols_arg:
        requested = [c.strip() for c in cols_arg.split(",") if c.strip()]
        unknown = [c for c in requested if c not in table.c]
        if unknown:
            abort(400, description=f"未知导出列: {', '.join(unknown)}")
        columns = requested
    return columns, [dict(r) for r in rows], truncated


def _label_column(table: Table):
    """外键下拉的业务标签列：优先字符串型非主键/非归属列，再退到首列/主键。"""
    pk_name = None
    pks = list(table.primary_key.columns)
    if len(pks) == 1:
        pk_name = pks[0].name
    for col in table.columns:
        if col.name == pk_name or col.name in _OWNERSHIP_COLS:
            continue
        tname = str(col.type).upper()
        if tname.startswith(("VARCHAR", "CHAR", "TEXT", "STRING", "ENUM", "UUID")):
            return col.name
    for col in table.columns:
        if col.name != pk_name and col.name not in _OWNERSHIP_COLS:
            return col.name
    return pk_name


def list_refs(registry: CrudRegistry, name: str, fk_col: str) -> dict:
    """外键下拉数据：返回被引用表 {value=pk, label=业务标签} 选项，支持 search/分页。"""
    table = registry.table(name)
    registry.require_action(name, "view")
    if fk_col not in table.c:
        abort(400, description=f"未知外键列: {fk_col}")
    fks = list(table.c[fk_col].foreign_keys)
    if not fks:
        abort(400, description=f"{fk_col} 不是外键列")
    target = fks[0].column.table
    pk_col = _pk_column(target)
    label_col = _label_column(target)

    search = (request.args.get("search") or "").strip()
    page = max(int(request.args.get("page", 1)), 1)
    page_size = min(max(int(request.args.get("page_size", 50)), 1), 200)

    stmt = select(target)
    total_stmt = select(func.count()).select_from(target)
    if search and label_col and label_col != pk_col.name:
        col = target.c[label_col]
        cond = col.ilike(f"%{search}%")
        stmt = stmt.where(cond)
        total_stmt = total_stmt.where(cond)

    with registry.engine.connect() as conn:
        total = conn.execute(total_stmt).scalar_one()
        rows = conn.execute(
            stmt.order_by(pk_col).limit(page_size).offset((page - 1) * page_size)
        ).mappings().all()

    return {
        "ref_table": target.name,
        "ref_column": pk_col.name,
        "label_column": label_col,
        "options": [
            {"value": r[pk_col.name],
             "label": r[label_col] if label_col else r[pk_col.name]}
            for r in rows
        ],
        "page": page,
        "page_size": page_size,
        "total": total,
    }


def attach_ref_labels(registry: CrudRegistry, name: str,
                      rows: list[dict]) -> dict:
    """为列表页行数据装配外键批量标签（0.15.0 M9-2 / DR-50）。

    对当前页可见的外键列收集去重 id，按列做一次 `WHERE <pk> IN (...)` 批量查询
    引用表显示列（复用 `_label_column` 推断），返回 `{列名: {id串: 标签}}`。

    - 仅当前页、仅外键列、id 去重 → 查询次数 = 可见外键列数（有界，非 N+1）；
    - 查不到的 id 不放入映射（前端兜底原值）；自引用外键列跳过；
    - 纯只读 SELECT，直连（link）模式同样可用（不触碰 DR-27 红线）。
    """
    table = registry.table(name)
    ref_labels: dict[str, dict[str, str]] = {}
    for col in table.columns:
        if not col.foreign_keys or col.name in _OWNERSHIP_COLS:
            continue
        target = next(iter(col.foreign_keys)).column.table
        if target.name == name:
            continue  # 自引用外键本页内解析，跳过批量查询
        pk_col = _pk_column(target)
        label_col = _label_column(target)
        ids = {
            str(r[col.name]) for r in rows
            if r.get(col.name) is not None and str(r[col.name]) != ""
        }
        if not ids:
            continue
        stmt = select(target.c[pk_col.name], target.c[label_col]).where(
            target.c[pk_col.name].in_([int(i) if i.isdigit() else i for i in ids])
        )
        label_map: dict[str, str] = {}
        with registry.engine.connect() as conn:
            for row in conn.execute(stmt).mappings():
                label_map[str(row[pk_col.name])] = (
                    row[label_col] if row[label_col] is not None else row[pk_col.name]
                )
        if label_map:
            ref_labels[col.name] = label_map
    return ref_labels


def get_stat(registry: CrudRegistry, name: str, aggregation: str,
             column: str | None = None) -> dict:
    """统计卡单值聚合（0.15.0 M9-6）：count / sum / avg，只读 SELECT。

    - 复用列表过滤/搜索/归属过滤（与 list_rows 同口径）；
    - sum/avg 必须指定数值列（非数值列 → 400）；
    - 纯只读，直连（link）模式同样可用（不触碰 DR-27 红线）。
    """
    table = registry.table(name)
    registry.require_action(name, "view")
    conditions = _filtered(table)
    owner = _owner_condition(registry, table)
    if owner is not None:
        conditions.append(owner)

    col = None
    if aggregation in ("sum", "avg"):
        if not column:
            abort(400, description="sum/avg 聚合必须指定目标列")
        if column not in table.c:
            abort(400, description=f"未知聚合列: {column}")
        col = table.c[column]
        tname = str(col.type).upper()
        if not tname.startswith(("INT", "DECIMAL", "NUMERIC", "FLOAT", "DOUBLE", "REAL")):
            abort(400, description=f"聚合列必须是数值列: {column}")
        expr = func.sum(col) if aggregation == "sum" else func.avg(col)
        stmt = select(expr).where(*conditions)
    else:
        stmt = select(func.count()).select_from(table).where(*conditions)

    with registry.engine.connect() as conn:
        value = conn.execute(stmt).scalar_one()
    if aggregation == "count":
        value = value or 0
    else:
        # DECIMAL 列聚合在 SQLite 返回 Decimal/字符串（如 30.00 / '30.00'），统一转数值
        try:
            from decimal import Decimal as _Decimal
            if isinstance(value, _Decimal):
                value = float(value)
            elif isinstance(value, str):
                value = float(value)
        except (ValueError, TypeError):
            pass
    return {"aggregation": aggregation, "column": column, "value": value}


# ---------------- 蓝图层（M0-4 demo / 离线测试复用） ----------------

def create_crud_blueprint(registry: CrudRegistry, url_prefix: str) -> Blueprint:
    bp = Blueprint(f"crud_{abs(id(registry))}", __name__, url_prefix=url_prefix)

    @bp.get("/meta")
    def meta():
        return jsonify(get_meta(registry))

    @bp.get("/tables/<name>/rows")
    def list_rows_route(name: str):
        return jsonify(list_rows(registry, name))

    @bp.get("/tables/<name>/rows/<pk>")
    def get_row_route(name: str, pk: str):
        return jsonify(get_row(registry, name, pk))

    @bp.post("/tables/<name>/rows")
    def create_row_route(name: str):
        return jsonify(create_row(registry, name)), 201

    @bp.put("/tables/<name>/rows/<pk>")
    def update_row_route(name: str, pk: str):
        return jsonify(update_row(registry, name, pk))

    @bp.delete("/tables/<name>/rows/<pk>")
    def delete_row_route(name: str, pk: str):
        return jsonify(delete_row(registry, name, pk))

    @bp.get("/tables/<name>/refs/<fk_col>")
    def refs_route(name: str, fk_col: str):
        return jsonify(list_refs(registry, name, fk_col))

    return bp