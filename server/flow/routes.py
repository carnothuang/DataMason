"""流程路由（M3-4，DR-06）：

工程室编排（创建人独有 DR-14）：
- GET    /deployments/{id}/flows                    流程清单（含步骤）
- POST   /deployments/{id}/flows                    新建流程（同步生成 kind=flow 页面供菜单挂载）
- PUT    /deployments/{id}/flows/{flow_id}          更新流程
- DELETE /deployments/{id}/flows/{flow_id}          删除流程（连带清理挂载菜单）

运行态向导页（成员）：
- GET    /app/{id}/flows/{flow_id}                  流程运行态（游标推导步骤状态 + 当前步条件）
- POST   /app/{id}/flows/{flow_id}/advance          下一步（条件不满足 409；写入状态字段 + activity_logs）
"""
from __future__ import annotations

from flask import Blueprint, abort, current_app, jsonify, request, session
from werkzeug.exceptions import Conflict, HTTPException

from server.flow import service
from server.flow.service import FlowError
from server.members.service import PermissionDenied
from server.models import Deployment

bp = Blueprint("flow", __name__)


def _uid() -> int:
    return session.get("user_id") or 1


def _require_creator(deployment_id: int) -> None:
    """工程室动作：仅创建人（DR-14，管理员/操作员无）。"""
    Session = current_app.extensions["datamason_session_factory"]
    with Session() as s:
        d = s.get(Deployment, deployment_id)
        if d is None:
            abort(404, description="部署不存在")
        from server.members import service as member_service

        if d.owner_id != _uid() and member_service.get_role(s, d, _uid()) != "creator":
            abort(403, description="仅创建人可进行工程室操作（管理员无此项能力）")


@bp.errorhandler(PermissionDenied)
def _permission_error(exc: PermissionDenied):
    return jsonify({"error": str(exc)}), 403


@bp.errorhandler(FlowError)
def _flow_error(exc: FlowError):
    return jsonify({"error": str(exc)}), 400


@bp.errorhandler(Conflict)
def _conflict(exc: Conflict):
    return jsonify({"error": exc.description}), 409


@bp.errorhandler(HTTPException)
def _http_error(exc: HTTPException):
    return jsonify({"error": exc.description}), exc.code


@bp.errorhandler(KeyError)
def _key_error(exc: KeyError):
    return jsonify({"error": str(exc)}), 404


@bp.errorhandler(Exception)
def _generic_error(exc: Exception):  # noqa: BLE001
    return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 400


# ---------------- 工程室编排（创建人独有，DR-14） ----------------

@bp.get("/deployments/<int:deployment_id>/flows")
def list_flows(deployment_id: int):
    _require_creator(deployment_id)
    return jsonify(service.list_flows(current_app, deployment_id))


@bp.post("/deployments/<int:deployment_id>/flows")
def create_flow(deployment_id: int):
    _require_creator(deployment_id)
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(service.save_flow(
            current_app, deployment_id, None,
            data.get("name"), data.get("steps"), _uid())), 201
    except FlowError as exc:
        return jsonify({"error": str(exc)}), 400


@bp.put("/deployments/<int:deployment_id>/flows/<int:flow_id>")
def update_flow(deployment_id: int, flow_id: int):
    _require_creator(deployment_id)
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(service.save_flow(
            current_app, deployment_id, flow_id,
            data.get("name"), data.get("steps"), _uid()))
    except FlowError as exc:
        return jsonify({"error": str(exc)}), 400


@bp.delete("/deployments/<int:deployment_id>/flows/<int:flow_id>")
def delete_flow(deployment_id: int, flow_id: int):
    _require_creator(deployment_id)
    return jsonify(service.delete_flow(current_app, deployment_id, flow_id, _uid()))


# ---------------- 运行态向导 ----------------

@bp.get("/app/<int:deployment_id>/flows/<int:flow_id>")
def runtime_flow(deployment_id: int, flow_id: int):
    return jsonify(service.runtime_flow(current_app, deployment_id, flow_id, _uid()))


@bp.post("/app/<int:deployment_id>/flows/<int:flow_id>/advance")
def advance_flow(deployment_id: int, flow_id: int):
    return jsonify(service.advance_flow(current_app, deployment_id, flow_id, _uid()))