"""工作台运行时接口（M2-2 菜单树 + M2-3 CRUD + M2-5 按角色裁剪 + M2-6 操作日志）。

- GET  /app/<id>          ：部署信息 + 菜单树 + 页面规格（M2-2）
  - M2-5：非成员 403；操作员只见被授权菜单与「我的记录」对应内容
- GET/POST/PUT/DELETE 等  ：/app/<id>/api/* 业务 CRUD（M2-3/M2-5）
  - /meta                   全表列元数据（含外键对；操作员仅被授权表）
  - /tables/<name>/rows    列表（参数化+白名单+分页/排序/过滤）
  - /tables/<name>/rows/<pk> 单行 GET/PUT/DELETE
  - /tables/<name>/refs/<fk_col> 外键下拉数据
- GET /app/<id>/api/activity-logs：操作日志（M2-6）倒序分页 + 四维筛选；
  creator/admin 全量，operator 强制只含自己的动作
- 归属列写入身份取会话用户；成员/按人授权过滤（M2-5）；
  写路径两阶段日志钩子 DR-03（M2-6）。
"""
from __future__ import annotations

from werkzeug.exceptions import Conflict, HTTPException

from flask import Blueprint, current_app, g, jsonify, request, session

from server.audit.service import AuditSession
from server.crud import engine as crud_engine
from server.crud import runtime
from server.members import service as member_service
from server.members.service import PermissionDenied
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
        uid = session.get("user_id") or 1
        role = member_service.assert_member(s, d, uid)  # 非成员 → PermissionDenied → 403

        if d.status == "stopped":
            # M3-1：停止态入口直达「系统已停止」提示页（数据保留，创建人可再启动）
            return jsonify({
                "deployment_id": deployment_id,
                "biz_name": d.biz_name,
                "status": "stopped",
                "data_mode": d.data_mode,
                "entry_path": d.entry_path,
                "role": role,
                "menus": [],
                "pages": [],
            })
        if d.status != "running":
            return jsonify({"error": f"该部署尚未就绪（状态：{d.status}），请回到控制台查看"}), 409

        menus = (
            s.query(DeploymentMenu)
            .filter_by(deployment_id=deployment_id)
            .order_by(DeploymentMenu.order_index, DeploymentMenu.id)
            .all()
        )
        if role == "operator":
            # 功能级裁剪（DR-02）：操作员可见菜单=自己的授权集合（含祖先分组）
            granted = member_service.granted_menu_ids(s, d, uid)
            visible = member_service.visible_menu_ids(menus, granted)
            menus = [m for m in menus if m.id in visible]

        visible_ref_ids = {m.ref_id for m in menus if m.ref_id}
        pages = (
            s.query(PageSpec)
            .filter_by(deployment_id=deployment_id)
            .order_by(PageSpec.id)
            .all()
        )
        if role == "operator":
            # 页面同菜单一起裁剪：只保留被授权菜单指向的页面
            pages = [p for p in pages if p.id in visible_ref_ids]
        tables = {
            t.id: t
            for t in s.query(TableSpec).filter_by(deployment_id=deployment_id).all()
        }
        # M5-C3（DR-30）：向每个页面下发其背后表的允许动作与数据范围，前端据此裁剪按钮
        access_map = (
            member_service.table_access_map(s, d, uid) if role == "operator" else None
        )

        def _page_access(table_name: str | None) -> tuple[list[str], str]:
            if role in ("creator", "admin"):
                return sorted(member_service.ACTION_KEYS), "all"
            if access_map and table_name and table_name in access_map:
                info = access_map[table_name]
                return sorted(info["actions"]), info["data_scope"]
            return [], "own"

        pages_payload = []
        for p in pages:
            tname = tables[p.table_id].name if (p.table_id and p.table_id in tables) else None
            actions, scope = _page_access(tname)
            pages_payload.append({
                "id": p.id,
                "kind": p.kind,
                "access": p.access,
                "title": p.title,
                "flow_id": p.flow_id,
                "table": tname,
                "table_biz_name": tables[p.table_id].biz_name
                if (p.table_id and p.table_id in tables)
                else None,
                "actions": actions,
                "data_scope": scope,
            })
        payload = {
            "deployment_id": deployment_id,
            "biz_name": d.biz_name,
            "status": d.status,
            "data_mode": d.data_mode,
            "entry_path": d.entry_path,
            "role": role,
            "menus": _menu_tree(menus),
            "pages": pages_payload,
        }
    return jsonify(payload)


# ---------------- M2-3 业务 CRUD（挂 /app/<id>/api/） ----------------

def _session_user_id() -> int:
    return session.get("user_id") or 1


_STATUS_LABEL = {"running": "运行中", "stopped": "已停止", "deploying": "部署中",
                 "draft": "草稿", "destroyed": "已销毁"}


def _assert_running(deployment_id: int) -> None:
    """M3-1：工作台 CRUD/日志仅运行态可用；停止态入口不可操作（数据保留可再启动）。"""
    Session = current_app.extensions["datamason_session_factory"]
    with Session() as s:
        d = s.get(Deployment, deployment_id)
        if d is None:
            raise KeyError(deployment_id)
        if d.status != "running":
            label = _STATUS_LABEL.get(d.status, d.status)
            hint = ("系统已停止，请返回控制台再启动后使用"
                    if d.status == "stopped" else "请返回控制台查看")
            raise Conflict(description=f"当前状态「{label}」，{hint}")


def _deployment_api(deployment_id: int):
    """按部署构建 registry + engine，调用方负责 dispose engine。
    M3-1：非运行态直接 409（停止后入口不可操作）。
    M3-8：API key 请求走服务身份（全表、归属列记系统 0），否则用会话用户（含成员/操作员过滤）。"""
    _assert_running(deployment_id)
    if getattr(g, "api_key", None) is not None:
        _d, registry, engine = runtime.registry_for_service(current_app, deployment_id)
    else:
        _d, registry, engine = runtime._registry(
            current_app, deployment_id, _session_user_id()
        )
    return registry, engine


def _audit(deployment_id: int) -> AuditSession:
    """构造两阶段日志会话（M2-6，DR-03）：actor=会话用户，source_ip=请求来源。
    M3-8：API key 请求无用户，actor 标记 key 名（actor_label）。"""
    ctx = getattr(g, "api_key", None)
    if ctx is not None:
        return AuditSession(
            current_app, deployment_id, None, request.remote_addr or "",
            actor_label=f"API key「{ctx['name']}」",
        )
    return AuditSession(
        current_app, deployment_id,
        _session_user_id(), request.remote_addr or "",
    )


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
    # M5-C3：按表下发允许动作与数据范围（会话用户；API key 服务身份不限制）
    if getattr(g, "api_key", None) is None:
        with Session() as s:
            d = s.get(Deployment, deployment_id)
            role = member_service.get_role(s, d, _session_user_id()) if d else None
            if role in ("creator", "admin"):
                for info in raw.values():
                    info["actions"] = sorted(member_service.ACTION_KEYS)
                    info["data_scope"] = "all"
            elif role == "operator" and d is not None:
                access = member_service.table_access_map(s, d, _session_user_id())
                for table_name, info in raw.items():
                    a = access.get(table_name)
                    info["actions"] = sorted(a["actions"]) if a else []
                    info["data_scope"] = a["data_scope"] if a else "own"
    return jsonify(raw)


@bp.get("/<int:deployment_id>/api/tables/<name>/rows")
def app_list_rows(deployment_id: int, name: str):
    registry, engine = _deployment_api(deployment_id)
    try:
        return jsonify(crud_engine.list_rows(registry, name))
    finally:
        engine.dispose()


@bp.get("/<int:deployment_id>/api/tables/<name>/rows.csv")
def app_export_rows(deployment_id: int, name: str):
    """导出 CSV（A6）：沿用列表的搜索/过滤/排序，UTF-8 BOM 便于 Excel 直开。"""
    import csv
    import io

    registry, engine = _deployment_api(deployment_id)
    try:
        columns, rows = crud_engine.export_rows(registry, name)
    finally:
        engine.dispose()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    for r in rows:
        writer.writerow(["" if r.get(c) is None else r.get(c) for c in columns])
    resp = current_app.response_class("\ufeff" + buf.getvalue(), mimetype="text/csv")
    resp.headers["Content-Disposition"] = f'attachment; filename="{name}.csv"'
    return resp


@bp.get("/<int:deployment_id>/api/tables/<name>/rows/<pk>")
def app_get_row(deployment_id: int, name: str, pk: str):
    registry, engine = _deployment_api(deployment_id)
    try:
        return jsonify(crud_engine.get_row(registry, name, pk))
    finally:
        engine.dispose()


@bp.get("/<int:deployment_id>/api/activity-logs")
def app_activity_logs(deployment_id: int):
    """操作日志（M2-6）：倒序分页 + 时间/人/动作/对象四维筛选；记录↔日志双向跳转复用。"""
    from server.audit import service as audit_service

    _assert_running(deployment_id)
    Session = current_app.extensions["datamason_session_factory"]
    with Session() as s:
        d = s.get(Deployment, deployment_id)
        if d is None:
            return jsonify({"error": "部署不存在"}), 404
        uid = _session_user_id()
        role = member_service.assert_member(s, d, uid)  # 非成员 → 403
    result = audit_service.list_logs(
        current_app, deployment_id, role, uid,
        actor_user_id=request.args.get("actor_user_id", type=int),
        action=request.args.get("action"),
        object_table=request.args.get("object_table"),
        object_pk=request.args.get("object_pk"),
        time_from=request.args.get("from"),
        time_to=request.args.get("to"),
        page=request.args.get("page", 1, type=int),
        page_size=request.args.get("page_size", 20, type=int),
    )
    with Session() as s:
        biz = {
            t.name: t.biz_name
            for t in s.query(TableSpec).filter_by(deployment_id=deployment_id).all()
        }
    for lg in result["logs"]:
        lg["object_biz_name"] = biz.get(lg["object_table"]) or lg["object_table"]
    return jsonify(result)


@bp.post("/<int:deployment_id>/api/tables/<name>/rows")
def app_create_row(deployment_id: int, name: str):
    registry, engine = _deployment_api(deployment_id)
    try:
        return jsonify(crud_engine.create_row(registry, name, audit=_audit(deployment_id))), 201
    finally:
        engine.dispose()


@bp.put("/<int:deployment_id>/api/tables/<name>/rows/<pk>")
def app_update_row(deployment_id: int, name: str, pk: str):
    registry, engine = _deployment_api(deployment_id)
    try:
        return jsonify(crud_engine.update_row(registry, name, pk, audit=_audit(deployment_id)))
    finally:
        engine.dispose()


@bp.delete("/<int:deployment_id>/api/tables/<name>/rows/<pk>")
def app_delete_row(deployment_id: int, name: str, pk: str):
    registry, engine = _deployment_api(deployment_id)
    try:
        return jsonify(crud_engine.delete_row(registry, name, pk, audit=_audit(deployment_id)))
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


@bp.errorhandler(PermissionDenied)
def _permission_error(exc: PermissionDenied):
    return jsonify({"error": str(exc)}), 403


@bp.errorhandler(KeyError)
def _key_error(exc: KeyError):
    return jsonify({"error": str(exc)}), 404


@bp.errorhandler(Exception)
def _generic_error(exc: Exception):  # noqa: BLE001
    return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 400