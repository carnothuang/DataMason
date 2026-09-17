"""按部署动态加载业务库的反射数据访问（M1-3 只读浏览；M2 接完整 CRUD/权限/日志）。

每次请求按 deployment_id 解析连接串（加密）→ 反射该部署声明的表 → 查询。
M1 不缓存 engine（部署数量小）；M2 按部署做 engine 池化。
M2-5：成员校验 + 操作员仅被授权表 + 「我的记录」owner_only（DR-02/DR-05）。
"""
from __future__ import annotations

from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import Engine

from server.crud.engine import CrudRegistry
from server.members import service as member_service
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


def _registry(app, deployment_id: int, current_user_id: int | None = None) -> tuple[Deployment, CrudRegistry, Engine]:
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        deployment = s.get(Deployment, deployment_id)
        if deployment is None:
            raise KeyError("部署不存在")
        uid = current_user_id
        role = None
        action_map = None
        scope_map = None
        if uid is not None:
            role = member_service.assert_member(s, deployment, uid)
        table_names = [
            t.name for t in s.query(TableSpec)
            .filter_by(deployment_id=deployment_id).all()
        ]
        if uid is not None and role == "operator":
            # 操作员只能操作被授权菜单背后的表（DR-02），且按表细化为动作集合/数据范围（M5-C）
            access = member_service.table_access_map(s, deployment, uid)
            table_names = [n for n in table_names if n in access]
            action_map = {n: set(v["actions"]) for n, v in access.items()}
            scope_map = {n: v["data_scope"] for n, v in access.items()}
    engine = _biz_engine(app, deployment)
    # 仅暴露该部署声明过的表（白名单）；归属列写入身份由会话注入（M2-3）；
    # 操作员「我的记录」强制 owner_only（M2-5，DR-05）；M5-C 起由 action_map/scope_map 细化
    registry = CrudRegistry(
        engine, tables=table_names,
        current_user_id=uid or 1,
        owner_only=(uid is not None and role == "operator"),
        action_map=action_map,
        scope_map=scope_map,
    )
    return deployment, registry, engine


def registry_for_service(app, deployment_id: int, actor_id: int = 0) -> tuple[Deployment, CrudRegistry, Engine]:
    """服务身份（M3-8 API key）：不查成员，暴露该部署全部声明表；归属列记 actor_id（默认 0=系统）。

    用于 API key 通道的 CRUD REST——key 是部署级服务凭证，行为等同管理员但不走用户会话。
    """
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        deployment = s.get(Deployment, deployment_id)
        if deployment is None:
            raise KeyError("部署不存在")
        table_names = [
            t.name for t in s.query(TableSpec).filter_by(deployment_id=deployment_id).all()
        ]
    engine = _biz_engine(app, deployment)
    registry = CrudRegistry(
        engine, tables=table_names, current_user_id=actor_id, owner_only=False,
    )
    return deployment, registry, engine


def list_tables(app, deployment_id: int, user_id: int | None = None) -> list[dict]:
    _d, registry, engine = _registry(app, deployment_id, user_id)
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
              order: str, filters: dict, user_id: int | None = None,
              search: str = "") -> dict:
    from sqlalchemy import asc, desc, or_

    _d, registry, engine = _registry(app, deployment_id, user_id)
    try:
        table = registry.table(table_name)
        conditions = []
        for key, value in filters.items():
            if key in table.c and value != "":
                conditions.append(table.c[key] == value)
        if registry.owner_only_for(table_name) and "created_by" in table.c:
            conditions.append(table.c.created_by == registry.current_user_id)
        if search:
            text_cols = [
                c for c in table.columns
                if str(c.type).upper().startswith(
                    ("VARCHAR", "CHAR", "TEXT", "STRING", "ENUM", "UUID"))
                and not c.primary_key
                and c.name not in ("created_by", "updated_by", "created_at", "updated_at")
            ]
            if text_cols:
                conditions.append(or_(*[c.ilike(f"%{search}%") for c in text_cols]))
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
