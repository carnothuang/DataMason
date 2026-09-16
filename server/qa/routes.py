"""问数据路由（M4-1/M4-3，P3）。

运行态（挂 /api/v1/app，登录 + 成员校验）：
- POST /app/{id}/qa/ask                     问一句 → 只读查询 + 结论 + 可选图表（M4-1）
- GET  /app/{id}/dashboards/{page_id}       钉住报表重跑（M4-3；操作员须被授权该菜单）

工程室（挂 /api/v1，创建人/管理员，DR-14）：
- POST   /deployments/{id}/qa/pin            钉住报表（写 dashboard 页 + 菜单）
- GET    /deployments/{id}/qa/pins           报表清单
- DELETE /deployments/{id}/qa/pins/{page_id} 取消钉住

错误：只读门/生成/执行 → 400；非运行态 → 409；非成员/操作员 → 403；部署/报表不存在 → 404。
"""
from __future__ import annotations

from flask import Blueprint, abort, current_app, jsonify, request, session
from werkzeug.exceptions import Conflict, HTTPException

from server.members import service as member_service
from server.members.service import PermissionDenied
from server.models import Deployment
from server.qa import service
from server.qa.service import QaError

bp = Blueprint("qa", __name__)
engineer_bp = Blueprint("qa_engineer", __name__)


def _uid() -> int:
    return session.get("user_id") or 1


def _require_pin_role(deployment_id: int) -> None:
    """钉住/取消/清单：仅创建人/管理员（操作员默认不开放问数据与报表）。"""
    Session = current_app.extensions["datamason_session_factory"]
    with Session() as s:
        d = s.get(Deployment, deployment_id)
        if d is None:
            abort(404, description="部署不存在")
        if member_service.assert_member(s, d, _uid()) == "operator":
            abort(403, description="问数据与报表仅创建人或管理员可用")


@bp.post("/<int:deployment_id>/qa/ask")
def qa_ask(deployment_id: int):
    data = request.get_json(silent=True) or {}
    return jsonify(service.ask(current_app, deployment_id, data.get("question"), _uid()))


@bp.get("/<int:deployment_id>/dashboards/<int:page_id>")
def dashboard_run(deployment_id: int, page_id: int):
    return jsonify(service.run_dashboard(current_app, deployment_id, page_id, _uid()))


@engineer_bp.post("/deployments/<int:deployment_id>/qa/pin")
def qa_pin(deployment_id: int):
    _require_pin_role(deployment_id)
    data = request.get_json(silent=True) or {}
    return jsonify(service.pin_report(
        current_app, deployment_id, _uid(),
        data.get("title"), data.get("sql"),
        chart=data.get("chart"), question=data.get("question") or "")), 201


@engineer_bp.get("/deployments/<int:deployment_id>/qa/pins")
def qa_pins(deployment_id: int):
    _require_pin_role(deployment_id)
    return jsonify(service.list_pins(current_app, deployment_id))


@engineer_bp.delete("/deployments/<int:deployment_id>/qa/pins/<int:page_id>")
def qa_unpin(deployment_id: int, page_id: int):
    _require_pin_role(deployment_id)
    return jsonify(service.unpin_report(current_app, deployment_id, page_id, _uid()))


# ---------------- 错误映射 ----------------

def _install_error_handlers(blueprint: Blueprint) -> None:
    @blueprint.errorhandler(PermissionDenied)
    def _permission_error(exc: PermissionDenied):
        return jsonify({"error": str(exc)}), 403

    @blueprint.errorhandler(Conflict)
    def _conflict(exc: Conflict):
        return jsonify({"error": exc.description}), 409

    @blueprint.errorhandler(QaError)
    def _qa_error(exc: QaError):
        return jsonify({"error": str(exc)}), 400

    @blueprint.errorhandler(KeyError)
    def _key_error(exc: KeyError):
        return jsonify({"error": str(exc)}), 404

    @blueprint.errorhandler(HTTPException)
    def _http_error(exc: HTTPException):
        return jsonify({"error": exc.description}), exc.code

    @blueprint.errorhandler(Exception)
    def _generic_error(exc: Exception):  # noqa: BLE001
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 400


_install_error_handlers(bp)
_install_error_handlers(engineer_bp)
