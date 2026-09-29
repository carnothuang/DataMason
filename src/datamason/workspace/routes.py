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

from datamason.audit.service import AuditSession
from datamason.crud import engine as crud_engine
from datamason.crud import runtime
from datamason.members import service as member_service
from datamason.members.service import PermissionDenied
from datamason.models import Deployment, PageSpec
from datamason.workspace import service

bp = Blueprint("workspace", __name__)


@bp.get("/<int:deployment_id>")
def get_workspace(deployment_id: int):
    Session = current_app.extensions["datamason_session_factory"]
    with Session() as s:
        d = s.get(Deployment, deployment_id)
        if d is None:
            return jsonify({"error": "部署不存在"}), 404
        uid = _session_user_id()
        role = member_service.assert_member(s, d, uid)  # 非成员 → PermissionDenied → 403

        # M3-1：停止态入口直达「系统已停止」提示页（数据保留，创建人可再启动）
        if d.status == "stopped":
            return jsonify(service.stopped_payload(d, role))
        if d.status != "running":
            return jsonify({"error": f"该部署尚未就绪（状态：{d.status}），请回到控制台查看"}), 409

        payload = service.build_payload(s, d, role, uid)
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
    # 富化：业务名列/归属列标记/长度 + 按表动作与数据范围（会话用户；API key 服务身份不限制）
    Session = current_app.extensions["datamason_session_factory"]
    with Session() as s:
        if getattr(g, "api_key", None) is None:
            d = s.get(Deployment, deployment_id)
            role = member_service.get_role(s, d, _session_user_id()) if d else None
        else:
            d, role = None, None
        service.enrich_meta(s, raw, deployment_id, role, _session_user_id(), d)
    return jsonify(raw)


@bp.get("/<int:deployment_id>/api/tables/<name>/rows")
def app_list_rows(deployment_id: int, name: str):
    registry, engine = _deployment_api(deployment_id)
    try:
        result = crud_engine.list_rows(registry, name)
        # 0.15.0 M9-2：外键列批量标签（读侧渲染，不改库存值）
        result["ref_labels"] = crud_engine.attach_ref_labels(
            registry, name, result["data"]
        )
        return jsonify(result)
    finally:
        engine.dispose()


@bp.get("/<int:deployment_id>/api/tables/<name>/rows.csv")
def app_export_rows(deployment_id: int, name: str):
    """导出 CSV（A6）：沿用列表的搜索/过滤/排序，UTF-8 BOM 便于 Excel 直开。"""
    registry, engine = _deployment_api(deployment_id)
    try:
        columns, rows, truncated = crud_engine.export_rows(
            registry, name, int(current_app.config.get("DM_EXPORT_MAX_ROWS", 20000)))
    finally:
        engine.dispose()
    resp = current_app.response_class(
        "\ufeff" + service.rows_to_csv(columns, rows), mimetype="text/csv")
    resp.headers["Content-Disposition"] = f'attachment; filename="{name}.csv"'
    resp.headers["X-DM-Truncated"] = "true" if truncated else "false"
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
    from datamason.audit import service as audit_service

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
        service.enrich_activity_logs(s, deployment_id, result)
    return jsonify(result)


# ---------------- 0.15.0 M9-6 运行态只读布局与统计 ----------------

@bp.get("/<int:deployment_id>/api/pages/<int:page_id>/layout")
def runtime_page_layout(deployment_id: int, page_id: int):
    """运行态只读布局（任意成员）：页面布局 + 涉及表的表级 UI 配置合并下发。"""
    Session = current_app.extensions["datamason_session_factory"]
    with Session() as s:
        d = s.get(Deployment, deployment_id)
        if d is None:
            raise KeyError(deployment_id)
        member_service.assert_member(s, d, _session_user_id())
        page = s.get(PageSpec, page_id)
        if page is None or page.deployment_id != deployment_id:
            raise KeyError(page_id)
        layout = service.get_page_layout(current_app, deployment_id, page_id)
        table_names = {
            b.get("props", {}).get("table")
            for b in layout["blocks"]
            if b.get("props", {}).get("table")
        }
    ui_configs: dict[str, dict] = {}
    for tname in table_names:
        ui_configs[tname] = service.get_table_ui_config(current_app, deployment_id, tname)
    return jsonify({**layout, "ui_configs": ui_configs})


@bp.get("/<int:deployment_id>/api/tables/<name>/stat")
def app_get_stat(deployment_id: int, name: str):
    """统计卡单值聚合（只读；复用列表过滤口径）。"""
    registry, engine = _deployment_api(deployment_id)
    try:
        return jsonify(crud_engine.get_stat(
            registry, name,
            request.args.get("aggregation", "count"),
            request.args.get("column") or None,
        ))
    finally:
        engine.dispose()


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