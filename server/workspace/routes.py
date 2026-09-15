"""工作台运行时接口（M2-2 菜单树 + M2-3 CRUD）。

- GET  /app/<id>          ：部署信息 + 菜单树 + 页面规格（M2-2）
- GET/POST/PUT/DELETE 等  ：/app/<id>/api/* 业务 CRUD（M2-3）
  - /meta                   全表列元数据（含外键对）
  - /tables/<name>/rows    列表（参数化+白名单+分页/排序/过滤）
  - /tables/<name>/rows/<pk> 单行 GET/PUT/DELETE
  - /tables/<name>/refs/<fk_col> 外键下拉数据
- 归属列写入身份取会话用户；成员/按人授权过滤在 M2-5 接入。
"""
from __future__ import annotations

from werkzeug.exceptions import HTTPException

from flask import Blueprint, current_app, jsonify, request, session

from server.crud import engine as crud_engine
from server.crud import runtime
from server.models import Deployment, DeploymentMenu, PageSpec, TableSpec

bp = Blueprint("workspace", __name__)


def _menu_tree(menus: list[DeploymentMenu]) -> list[dict]:
    """把平铺菜单（按 id 排序）构造成树：parent_id 指向本集合内的菜单。

    孤儿（parent 不在集合内）按根节点处理，避免丢失。
    """
    nodes = {
        m.id: {
            "id": m.id,
            "label": m.label,
            "kind": m.kind,
            "ref_id": m.ref_id,
            "children": [],
        }
        for m in menus
    }
    roots: list[dict] = []
    for m in menus:
        node = nodes[m.id]
        parent = nodes.get(m.parent_id)
        if m.parent_id and parent is not None:
            parent["children"].append(node)
        else:
            roots.append(node)
    return roots


@bp.get("/<int:deployment_id>")
def get_workspace(deployment_id: int):
    Session = current_app.extensions["datamason_session_factory"]
    with Session() as s:
        d = s.get(Deployment, deployment_id)
        if d is None:
            return jsonify({"error": "部署不存在"}), 404
        if d.status != "running":
            return jsonify({"error": f"该部署尚未就绪（状态：{d.status}），请回到控制台查看"}), 409
        menus = (
            s.query(DeploymentMenu)
            .filter_by(deployment_id=deployment_id)
            .order_by(DeploymentMenu.id)
            .all()
        )
        pages = (
            s.query(PageSpec)
            .filter_by(deployment_id=deployment_id)
            .order_by(PageSpec.id)
            .all()
        )
        tables = {
            t.id: t
            for t in s.query(TableSpec).filter_by(deployment_id=deployment_id).all()
        }
        payload = {
            "deployment_id": deployment_id,
            "biz_name": d.biz_name,
            "status": d.status,
            "entry_path": d.entry_path,
            "menus": _menu_tree(menus),
            "pages": [
                {
                    "id": p.id,
                    "kind": p.kind,
                    "access": p.access,
                    "title": p.title,
                    "table": tables[p.table_id].name
                    if (p.table_id and p.table_id in tables)
                    else None,
                    "table_biz_name": tables[p.table_id].biz_name
                    if (p.table_id and p.table_id in tables)
                    else None,
                }
                for p in pages
            ],
        }
    return jsonify(payload)


# ---------------- M2-3 业务 CRUD（挂 /app/<id>/api/） ----------------

def _session_user_id() -> int:
    return session.get("user_id") or 1


def _deployment_api(deployment_id: int):
    """按部署构建 registry + engine（会话用户注入归属列），调用方负责 dispose engine。"""
    _d, registry, engine = runtime._registry(
        current_app, deployment_id, _session_user_id()
    )
    return registry, engine


@bp.get("/<int:deployment_id>/api/meta")
def app_meta(deployment_id: int):
    registry, engine = _deployment_api(deployment_id)
    try:
        raw = crud_engine.get_meta(registry)
    finally:
        engine.dispose()
    # 富化：业务名列/归属列标记/长度（来自 table_specs，前端渲染表单与隐藏归属列）
    Session = current_app.extensions["datamason_session_factory"]
    with Session() as s:
        specs = {
            t.name: {c["name"]: c for c in t.columns}
            for t in s.query(TableSpec).filter_by(deployment_id=deployment_id).all()
        }
    for table_name, info in raw.items():
        spec_cols = specs.get(table_name, {})
        for c in info["columns"]:
            sc = spec_cols.get(c["name"], {})
            c["biz_name"] = sc.get("biz_name") or c["name"]
            c["is_ownership"] = bool(sc.get("is_ownership"))
            # 类型取约定语义类型（BOOLEAN 在 SQLite 反射为 INTEGER 会丢语义）
            if sc.get("type"):
                c["type"] = sc["type"]
            if c["length"] is None:
                c["length"] = sc.get("length")
    return jsonify(raw)


@bp.get("/<int:deployment_id>/api/tables/<name>/rows")
def app_list_rows(deployment_id: int, name: str):
    registry, engine = _deployment_api(deployment_id)
    try:
        return jsonify(crud_engine.list_rows(registry, name))
    finally:
        engine.dispose()


@bp.get("/<int:deployment_id>/api/tables/<name>/rows/<pk>")
def app_get_row(deployment_id: int, name: str, pk: str):
    registry, engine = _deployment_api(deployment_id)
    try:
        return jsonify(crud_engine.get_row(registry, name, pk))
    finally:
        engine.dispose()


@bp.post("/<int:deployment_id>/api/tables/<name>/rows")
def app_create_row(deployment_id: int, name: str):
    registry, engine = _deployment_api(deployment_id)
    try:
        return jsonify(crud_engine.create_row(registry, name)), 201
    finally:
        engine.dispose()


@bp.put("/<int:deployment_id>/api/tables/<name>/rows/<pk>")
def app_update_row(deployment_id: int, name: str, pk: str):
    registry, engine = _deployment_api(deployment_id)
    try:
        return jsonify(crud_engine.update_row(registry, name, pk))
    finally:
        engine.dispose()


@bp.delete("/<int:deployment_id>/api/tables/<name>/rows/<pk>")
def app_delete_row(deployment_id: int, name: str, pk: str):
    registry, engine = _deployment_api(deployment_id)
    try:
        return jsonify(crud_engine.delete_row(registry, name, pk))
    finally:
        engine.dispose()


@bp.get("/<int:deployment_id>/api/tables/<name>/refs/<fk_col>")
def app_refs(deployment_id: int, name: str, fk_col: str):
    registry, engine = _deployment_api(deployment_id)
    try:
        return jsonify(crud_engine.list_refs(registry, name, fk_col))
    finally:
        engine.dispose()


@bp.errorhandler(HTTPException)
def _http_error(exc: HTTPException):
    return jsonify({"error": exc.description}), exc.code


@bp.errorhandler(KeyError)
def _key_error(exc: KeyError):
    return jsonify({"error": str(exc)}), 404


@bp.errorhandler(Exception)
def _generic_error(exc: Exception):  # noqa: BLE001
    return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 400