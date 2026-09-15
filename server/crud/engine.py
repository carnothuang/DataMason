"""元数据驱动的反射 CRUD 引擎（设计细化 §十一·五）。

- SQLAlchemy Core 反射真实 schema → 动态 Table 对象（不为每张表写 model/代码）
- PostgREST 风格 REST：/tables/{name}/rows，参数化查询，列白名单
- M0-4：以 CrudRegistry 注册「engine + 表白名单」，可挂任意蓝图前缀；
  M2 演进为按 /app/{deployment_id}/api 挂载并叠加权限/日志钩子。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from flask import Blueprint, abort, jsonify, request
from sqlalchemy import MetaData, Table, asc, desc, func, select
from sqlalchemy.engine import Engine


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CrudRegistry:
    """一个 registry 绑定一个物理库 engine 与允许暴露的表集合。

    current_user_id：归属列（created_by/updated_by，DR-05）的写入身份；
    M0 demo 默认 1（系统/首管理员），M1 起由认证会话注入真实 users.id。
    """

    def __init__(
        self,
        engine: Engine,
        tables: Iterable[str] | None = None,
        current_user_id: int = 1,
    ):
        self.engine = engine
        self.current_user_id = current_user_id
        self.meta = MetaData()
        allowed = list(tables) if tables is not None else None
        self.meta.reflect(bind=engine, only=allowed if allowed else None)
        # only= 参数可能带 schema，表名取最后一段
        self.allowed_tables = {t.split(".")[-1] for t in self.meta.tables}

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
    """简单类型还原：bool/int/float/str。交由数据库做最终类型校验。"""
    tname = str(column.type).upper()
    if raw is None:
        return None
    if "INT" in tname and "BIGINT" not in tname:
        return int(raw)
    if "BOOL" in tname or tname.startswith("TINYINT(1)"):
        return str(raw).lower() in ("1", "true", "yes")
    return raw


def _filtered(table):
    """根据查询参数构造 WHERE：仅允许表内列、全部参数化。"""
    conditions = []
    reserved = {"page", "page_size", "sort", "order", "search"}
    for key, value in request.args.items():
        if key in reserved:
            continue
        if key not in table.c:
            abort(400, description=f"未知过滤列: {key}")
        conditions.append(table.c[key] == value)
    return conditions


def create_crud_blueprint(registry: CrudRegistry, url_prefix: str) -> Blueprint:
    bp = Blueprint(f"crud_{abs(id(registry))}", __name__, url_prefix=url_prefix)

    @bp.get("/meta")
    def meta():
        return jsonify(
            {
                name.split(".")[-1]: {
                    "columns": [
                        {
                            "name": c.name,
                            "type": str(c.type),
                            "nullable": c.nullable,
                            "pk": c.primary_key,
                        }
                        for c in registry.table(name.split(".")[-1]).columns
                    ]
                }
                for name in registry.meta.tables
            }
        )

    @bp.get("/tables/<name>/rows")
    def list_rows(name: str):
        table = registry.table(name)
        page = max(int(request.args.get("page", 1)), 1)
        page_size = min(max(int(request.args.get("page_size", 20)), 1), 200)

        stmt = select(table).where(*_filtered(table))

        sort = request.args.get("sort")
        if sort:
            if sort not in table.c:
                abort(400, description=f"未知排序列: {sort}")
            direction = desc if request.args.get("order", "asc") == "desc" else asc
            stmt = stmt.order_by(direction(table.c[sort]))

        with registry.engine.connect() as conn:
            total = conn.execute(
                select(func.count()).select_from(table).where(*_filtered(table))
            ).scalar_one()
            rows = conn.execute(
                stmt.limit(page_size).offset((page - 1) * page_size)
            ).mappings().all()

        return jsonify(
            {
                "data": [dict(r) for r in rows],
                "page": page,
                "page_size": page_size,
                "total": total,
            }
        )

    @bp.get("/tables/<name>/rows/<pk>")
    def get_row(name: str, pk: str):
        table = registry.table(name)
        pk_col = _pk_column(table)
        with registry.engine.connect() as conn:
            row = conn.execute(
                select(table).where(pk_col == _coerce_value(pk_col, pk))
            ).mappings().first()
        if row is None:
            abort(404)
        return jsonify(dict(row))

    @bp.post("/tables/<name>/rows")
    def create_row(name: str):
        table = registry.table(name)
        payload = request.get_json(force=True) or {}
        values = {}
        for key, value in payload.items():
            if key in table.c and not table.c[key].primary_key:
                values[key] = _coerce_value(table.c[key], value)
        # 归属列自动注入（DR-05）：业务表强制四列，前端不传也补齐
        now = _utcnow()
        if "created_by" in table.c:
            values.setdefault("created_by", registry.current_user_id)
        if "created_at" in table.c:
            values.setdefault("created_at", now)
        if "updated_by" in table.c:
            values.setdefault("updated_by", registry.current_user_id)
        if "updated_at" in table.c:
            values.setdefault("updated_at", now)
        with registry.engine.begin() as conn:
            result = conn.execute(table.insert().values(**values))
            pk = result.inserted_primary_key[0]
        return jsonify({"id": pk}), 201

    @bp.put("/tables/<name>/rows/<pk>")
    def update_row(name: str, pk: str):
        table = registry.table(name)
        pk_col = _pk_column(table)
        payload = request.get_json(force=True) or {}
        values = {}
        for key, value in payload.items():
            if key in table.c and not table.c[key].primary_key:
                values[key] = _coerce_value(table.c[key], value)
        if "updated_by" in table.c:
            values["updated_by"] = registry.current_user_id
        if "updated_at" in table.c:
            values["updated_at"] = _utcnow()
        if not values:
            abort(400, description="没有可更新的列")
        with registry.engine.begin() as conn:
            conn.execute(
                table.update().where(pk_col == _coerce_value(pk_col, pk)).values(**values)
            )
        return jsonify({"updated": True})

    @bp.delete("/tables/<name>/rows/<pk>")
    def delete_row(name: str, pk: str):
        table = registry.table(name)
        pk_col = _pk_column(table)
        with registry.engine.begin() as conn:
            conn.execute(
                table.delete().where(pk_col == _coerce_value(pk_col, pk))
            )
        return jsonify({"deleted": True})

    return bp
