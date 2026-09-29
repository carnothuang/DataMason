"""sql_import：专业模式把人写的 CREATE TABLE 解析归一化为 SchemaSpec（0.14.0 / DR-42）。

纯领域模块（不 import 蓝图 / request）。对齐 DR-42：用户 DDL 不直接执行，先用
sqlglot 按目标方言解析，抽取表/列/主键/外键/可空/唯一/自增，把列类型收敛到
平台八类，再由 `ddl_builder` 确定性生成目标方言 DDL 落库。

- `parse_create_tables(sql, dialect)` → SchemaSpec（缺主键的表由系统补 id）。
- 非建表语句、不支持类型、复合主键、重名列、悬空外键等，抛 `SqlImportError`，
  `errors` 为定位到语句/表/列的可读清单 {table, column, message}。
"""
from __future__ import annotations

import sqlglot
from pydantic import ValidationError
from sqlglot import exp
from sqlglot.errors import ParseError

from datamason.ddl.table_spec import ColumnSpec, SchemaSpec, TableSpecModel

SUPPORTED = ("sqlite", "mysql", "postgresql")

# 平台方言名 → sqlglot 方言名
_DIALECT_READ = {"sqlite": "sqlite", "mysql": "mysql", "postgresql": "postgres"}

# sqlglot DType → 平台列类型
_TYPE_MAP: dict[str, str] = {
    "INT": "INTEGER",
    "INTEGER": "INTEGER",
    "MEDIUMINT": "INTEGER",
    "SMALLINT": "INTEGER",
    "TINYINT": "INTEGER",
    "BIGINT": "BIGINT",
    "VARCHAR": "VARCHAR",
    "CHAR": "VARCHAR",
    "NVARCHAR": "VARCHAR",
    "TEXT": "TEXT",
    "TINYTEXT": "TEXT",
    "MEDIUMTEXT": "TEXT",
    "LONGTEXT": "TEXT",
    "CLOB": "TEXT",
    "BOOLEAN": "BOOLEAN",
    "BOOL": "BOOLEAN",
    "DATETIME": "DATETIME",
    "TIMESTAMP": "DATETIME",
    "DECIMAL": "DECIMAL",
    "NUMERIC": "DECIMAL",
}


class SqlImportError(ValueError):
    """解析失败：携带定位到语句/表/列的可读错误清单。"""

    def __init__(self, errors: list[dict]):
        self.errors = errors
        detail = "；".join(e.get("message", "") for e in errors) or "SQL 解析失败"
        super().__init__(detail)


def _err(table: str | None, column: str | None, message: str) -> dict:
    return {"table": table, "column": column, "message": message}


def _dtype_key(dt: exp.DataType) -> str:
    this = dt.this
    return this.name if this is not None else ""


def _type_params(dt: exp.DataType) -> list[int]:
    out: list[int] = []
    for p in dt.args.get("expressions") or []:
        lit = p.this if isinstance(p, exp.DataTypeParam) else p
        if isinstance(lit, exp.Literal) and lit.is_int:
            out.append(int(lit.this))
    return out


def _map_type(
    table: str, col: str, dt: exp.DataType
) -> tuple[str, int | None, bool]:
    """返回 (ColumnType, length, autoincrement)；不支持时记录错误（bad=True）。"""
    key = _dtype_key(dt)
    if key in ("SERIAL", "BIGSERIAL"):
        return ("BIGINT" if key == "BIGSERIAL" else "INTEGER", None, True)
    mapped = _TYPE_MAP.get(key)
    if not mapped:
        raise SqlImportError([_err(
            table, col,
            f"表 {table} 列 {col} 的类型 {dt.sql()} 不受支持，"
            "请改用 INTEGER/BIGINT/VARCHAR/TEXT/BOOLEAN/DATETIME/DECIMAL",
        )])
    length: int | None = None
    params = _type_params(dt)
    if mapped == "VARCHAR":
        length = params[0] if params else 255
        if not 1 <= length <= 4000:
            raise SqlImportError([_err(
                table, col,
                f"表 {table} 列 {col} 的长度 {length} 越界，需在 1–4000 之间",
            )])
    elif key == "TINYINT" and params and params[0] == 1:
        mapped = "BOOLEAN"
        length = None
    return (mapped, length, False)


def _reference_target(ref: exp.Reference) -> tuple[str, str | None]:
    schema = ref.this
    table_node = schema.this
    table = table_node.name if hasattr(table_node, "name") else table_node.sql()
    cols = schema.expressions
    column = cols[0].name if cols else None
    return table, column


def _table_name(create: exp.Create) -> str:
    node = create.this.this
    return node.name if hasattr(node, "name") else node.sql()


def parse_create_tables(sql: str, dialect: str) -> SchemaSpec:
    """把一条或多条 CREATE TABLE 解析为 SchemaSpec（不执行、不落库）。"""
    if dialect not in SUPPORTED:
        raise SqlImportError([_err(None, None, f"不支持的方言：{dialect}")])

    try:
        statements = sqlglot.parse(sql, read=_DIALECT_READ[dialect])
    except ParseError as exc:
        raise SqlImportError([_err(None, None, f"SQL 语法解析失败：{exc}")]) from exc

    errors: list[dict] = []
    parsed: list[tuple[str, dict | None, list[ColumnSpec], dict]] = []

    for stmt in statements:
        if stmt is None:
            continue
        if not isinstance(stmt, exp.Create) or not isinstance(stmt.this, exp.Schema):
            errors.append(_err(
                None, None,
                f"仅允许 CREATE TABLE 建表语句，发现 {stmt.sql()[:40]}",
            ))
            continue
        table = _table_name(stmt)
        cols: list[ColumnSpec] = []
        fk_constraints: list[tuple[str, str, str | None]] = []
        table_pk: list[str] = []
        bad = False
        for elem in stmt.this.expressions:
            if isinstance(elem, exp.ColumnDef):
                col = elem.name
                try:
                    ctype, length, autoinc = _map_type(table, col, elem.kind)
                except SqlImportError as exc:
                    errors.extend(exc.errors)
                    bad = True
                    continue
                nullable = True
                is_pk = False
                unique = False
                default = None
                for con in elem.constraints:
                    kind = con.kind
                    if isinstance(kind, exp.NotNullColumnConstraint):
                        nullable = False
                    elif isinstance(kind, exp.PrimaryKeyColumnConstraint):
                        is_pk = True
                    elif isinstance(kind, exp.AutoIncrementColumnConstraint):
                        autoinc = True
                    elif isinstance(kind, exp.UniqueColumnConstraint):
                        unique = True
                    elif isinstance(kind, exp.DefaultColumnConstraint):
                        lit = kind.this
                        default = lit.name if isinstance(lit, exp.Literal) else lit.sql()
                    elif isinstance(kind, exp.Reference):
                        ref_t, ref_c = _reference_target(kind)
                        fk_constraints.append((col, ref_t, ref_c))
                try:
                    cols.append(ColumnSpec(
                        name=col, type=ctype, length=length, nullable=nullable,
                        is_pk=is_pk, autoincrement=autoinc, unique=unique,
                        default=default,
                    ))
                except ValidationError as exc:
                    msg = exc.errors()[0]["msg"]
                    errors.append(_err(table, col, f"表 {table} 列 {col}: {msg}"))
                    bad = True
            elif isinstance(elem, exp.PrimaryKey):
                names = [c.name for c in elem.expressions]
                if len(names) != 1:
                    errors.append(_err(
                        table, None,
                        f"表 {table} 使用了复合主键 {names}，平台仅支持单列主键",
                    ))
                    bad = True
                else:
                    table_pk.extend(names)
            elif isinstance(elem, exp.ForeignKey):
                names = [c.name for c in elem.expressions]
                ref = elem.args.get("reference")
                if len(names) != 1 or ref is None:
                    errors.append(_err(
                        table, None,
                        f"表 {table} 的外键 {elem.sql()[:40]} 必须是单列外键",
                    ))
                    bad = True
                else:
                    ref_t, ref_c = _reference_target(ref)
                    fk_constraints.append((names[0], ref_t, ref_c))
            elif isinstance(elem, exp.Constraint):
                fk_elems = [
                    x for x in elem.expressions if isinstance(x, exp.ForeignKey)
                ]
                if not fk_elems:
                    errors.append(_err(
                        table, None,
                        f"表 {table} 含本版不支持的命名约束 {elem.sql()[:40]}",
                    ))
                    bad = True
                for fk_elem in fk_elems:
                    names = [c.name for c in fk_elem.expressions]
                    ref = fk_elem.args.get("reference")
                    if len(names) != 1 or ref is None:
                        errors.append(_err(
                            table, None,
                            f"表 {table} 的外键 {elem.sql()[:40]} 必须是单列外键",
                        ))
                        bad = True
                    else:
                        ref_t, ref_c = _reference_target(ref)
                        fk_constraints.append((names[0], ref_t, ref_c))
            elif isinstance(elem, exp.UniqueColumnConstraint):
                target = elem.this
                sources = (
                    target.expressions if hasattr(target, "expressions")
                    and target.expressions else [target]
                )
                names = [
                    c.name if hasattr(c, "name") else c.this.name
                    for c in sources
                ]
                for n in names:
                    for c in cols:
                        if c.name == n:
                            c.unique = True
            else:
                errors.append(_err(
                    table, None,
                    f"表 {table} 含本版不支持的表定义 {elem.sql()[:40]}"
                    "（索引/约束/检查等），请仅保留列、主键、外键、单列唯一",
                ))
                bad = True

        parsed.append((table, None, cols, {
            "fks": fk_constraints, "table_pk": table_pk, "bad": bad,
        }))

    # 重名表
    names = [t for t, _, _, _ in parsed]
    dup = {n for n in names if names.count(n) > 1}
    for n in dup:
        errors.append(_err(n, None, f"存在重名表 {n}"))

    if errors:
        raise SqlImportError(errors)

    tables: list[TableSpecModel] = []
    tmap_cols: dict[str, dict[str, ColumnSpec]] = {}
    for table, _, cols, meta in parsed:
        colmap = {c.name: c for c in cols}
        tmap_cols[table] = colmap
        for pk in meta["table_pk"]:
            if pk in colmap:
                colmap[pk].is_pk = True

    for table, _, cols, meta in parsed:
        colmap = tmap_cols[table]
        for fcol, ref_t, ref_c in meta["fks"]:
            if fcol not in colmap:
                errors.append(_err(
                    table, fcol,
                    f"表 {table} 的外键列 {fcol} 不存在",
                ))
                continue
            target_col = ref_c or "id"
            if ref_t not in tmap_cols:
                errors.append(_err(
                    table, fcol,
                    f"表 {table}.{fcol} 引用了不存在的表 {ref_t}",
                ))
                continue
            if target_col not in tmap_cols[ref_t]:
                errors.append(_err(
                    table, fcol,
                    f"外键 {table}.{fcol} 的引用列 {ref_t}.{target_col} 不存在",
                ))
                continue
            if not tmap_cols[ref_t][target_col].is_pk:
                errors.append(_err(
                    table, fcol,
                    f"外键仅允许引用主键：{table}.{fcol} → {ref_t}.{target_col}",
                ))
                continue
            c = colmap[fcol]
            c.is_fk = True
            c.ref_table = ref_t
            c.ref_column = target_col

        # 主键：无则系统补 id；多个则报错
        pk_cols = [c for c in cols if c.is_pk]
        if len(pk_cols) > 1:
            errors.append(_err(table, None, f"表 {table} 存在多个主键，仅允许单列主键"))
        elif not pk_cols:
            cols = [
                ColumnSpec(
                    name="id", biz_name="编号", type="INTEGER", nullable=False,
                    is_pk=True, autoincrement=True,
                ),
                *cols,
            ]
        else:
            pk = pk_cols[0]
            pk.nullable = False
            if pk.type == "BIGINT":
                pk.autoincrement = True
        try:
            tables.append(TableSpecModel(name=table, columns=cols))
        except ValidationError as exc:
            for e in exc.errors():
                errors.append(_err(table, None, f"表 {table}: {e['msg']}"))

    if errors:
        raise SqlImportError(errors)

    try:
        return SchemaSpec(tables=tables)
    except ValidationError as exc:
        raise SqlImportError(
            [_err(None, None, str(e["msg"])) for e in exc.errors()]
        ) from exc
