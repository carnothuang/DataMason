"""按部署动态加载业务库的反射数据访问（M1-3 只读浏览；M2 接完整 CRUD/权限/日志）。

每次请求按 deployment_id 解析连接串（加密）→ 反射该部署声明的表 → 查询。
M1 不缓存 engine（部署数量小）；M2 按部署做 engine 池化。
"""
from __future__ import annotations

from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import Engine

from server.crud.engine import CrudRegistry
from server.models import Connection, Deployment, TableSpec


def _biz_engine(app, deployment: Deployment) -> Engine:
    cipher = app.extensions["cipher"]
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        conn = s.get(Connection, deployment.connection_id)
        if conn is None:
            raise KeyError("部署没有关联数据连接")
        uri = cipher.decrypt(conn.conn_uri)
    return create_engine(uri, future=True)


def _registry(app, deployment_id: int) -> tuple[Deployment, CrudRegistry, Engine]:
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        deployment = s.get(Deployment, deployment_id)
        if deployment is None:
            raise KeyError("部署不存在")
        table_names = [
            t.name for t in s.query(TableSpec)
            .filter_by(deployment_id=deployment_id).all()
        ]
    engine = _biz_engine(app, deployment)
    # 仅暴露该部署声明过的表（白名单）
    registry = CrudRegistry(engine, tables=table_names)
    return deployment, registry, engine


def list_tables(app, deployment_id: int) -> list[dict]:
    _d, registry, engine = _registry(app, deployment_id)
    try:
        result = []
        Session = app.extensions["datamason_session_factory"]
        with Session() as s:
            specs = {t.name: t for t in s.query(TableSpec)
                     .filter_by(deployment_id=deployment_id).all()}
        for name in sorted(registry.allowed_tables):
            table = registry.table(name)
            result.append({
                "name": name,
                "biz_name": specs.get(name).biz_name if specs.get(name) else name,
                "columns": [c.name for c in table.columns],
            })
        return result
    finally:
        engine.dispose()


def list_rows(app, deployment_id: int, table_name: str,
              page: int, page_size: int, sort: str | None,
              order: str, filters: dict) -> dict:
    from sqlalchemy import asc, desc

    _d, registry, engine = _registry(app, deployment_id)
    try:
        table = registry.table(table_name)
        conditions = []
        for key, value in filters.items():
            if key in table.c and value != "":
                conditions.append(table.c[key] == value)
        stmt = select(table).where(*conditions)
        if sort and sort in table.c:
            direction = desc if order == "desc" else asc
            stmt = stmt.order_by(direction(table.c[sort]))
        with engine.connect() as conn:
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
            "columns": [c.name for c in table.columns],
        }
    finally:
        engine.dispose()
