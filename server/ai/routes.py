"""AI 生成历史治理路由（M4-4）。

平台管理员（设置页，挂 /api/v1/settings）：
- GET    /ai-generations              列表（kind/deployment_id/status 筛选 + 统计）
- POST   /ai-generations/cleanup      清理失败/陈旧记录
- DELETE /ai-generations/<id>         删除单条

工作台工程室（创建人/管理员，挂 /api/v1）：
- GET /deployments/<id>/ai-generations 本部署生成历史
"""
from __future__ import annotations

from flask import Blueprint, abort, current_app, jsonify, request, session

from server.ai import service
from server.members import service as member_service
from server.models import Deployment

settings_bp = Blueprint("ai_settings", __name__)
deploy_bp = Blueprint("ai_deploy", __name__)


def _uid() -> int:
    return session.get("user_id") or 1


def _opt_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@settings_bp.get("/ai-generations")
def list_generations():
    return jsonify(service.list_generations(
        current_app,
        kind=request.args.get("kind"),
        deployment_id=_opt_int(request.args.get("deployment_id")),
        status=request.args.get("status"),
        page=request.args.get("page", 1, type=int),
        page_size=request.args.get("page_size", 20, type=int),
    ))


@settings_bp.post("/ai-generations/cleanup")
def cleanup():
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(service.cleanup(
            current_app,
            ids=data.get("ids"),
            kind=data.get("kind"),
            deployment_id=_opt_int(data.get("deployment_id")),
            status=data.get("status"),
            older_than_days=_opt_int(data.get("older_than_days")),
        ))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@settings_bp.delete("/ai-generations/<int:gen_id>")
def delete_generation(gen_id: int):
    try:
        return jsonify(service.delete_one(current_app, gen_id))
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404


@deploy_bp.get("/deployments/<int:deployment_id>/ai-generations")
def deployment_generations(deployment_id: int):
    """工作台工程室：本部署生成历史（创建人/管理员）。"""
    Session = current_app.extensions["datamason_session_factory"]
    with Session() as s:
        d = s.get(Deployment, deployment_id)
        if d is None:
            abort(404, description="部署不存在")
        if member_service.assert_member(s, d, _uid()) == "operator":
            abort(403, description="AI 生成历史仅创建人或管理员可见")
    return jsonify(service.list_generations(
        current_app, deployment_id=deployment_id,
        kind=request.args.get("kind"),
        status=request.args.get("status"),
        page=request.args.get("page", 1, type=int),
        page_size=request.args.get("page_size", 20, type=int),
    ))
