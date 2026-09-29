"""分享链接路由（M3-5，DR-04）。

- GET/POST/DELETE /deployments/{id}/share-links*：创建人独有（DR-14）
- POST /share/{token}/accept：任一登录用户进入链接即绑定为该部署成员
"""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request, session
from werkzeug.exceptions import HTTPException

from datamason.members import service as member_service
from datamason.members.service import PermissionDenied
from datamason.models import Deployment
from datamason.share import service
from datamason.share.service import ShareError

bp = Blueprint("share", __name__)


def _uid() -> int:
    return session.get("user_id") or 1


def _require_creator(s, deployment_id: int) -> Deployment:
    d = s.get(Deployment, deployment_id)
    if d is None:
        raise KeyError("部署不存在")
    if d.owner_id != _uid() and member_service.get_role(s, d, _uid()) != "creator":
        raise PermissionDenied("仅创建人可生成/管理分享链接（管理员无此项能力）")
    return d


@bp.errorhandler(PermissionDenied)
def _permission_error(exc: PermissionDenied):
    return jsonify({"error": str(exc)}), 403


@bp.errorhandler(ShareError)
def _share_error(exc: ShareError):
    return jsonify({"error": str(exc)}), 400


@bp.errorhandler(HTTPException)
def _http_error(exc: HTTPException):
    return jsonify({"error": exc.description}), exc.code


@bp.errorhandler(KeyError)
def _key_error(exc: KeyError):
    return jsonify({"error": str(exc)}), 404


@bp.errorhandler(Exception)
def _generic_error(exc: Exception):  # noqa: BLE001
    return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 400


@bp.get("/deployments/<int:deployment_id>/share-links")
def list_links(deployment_id: int):
    Session = current_app.extensions["datamason_session_factory"]
    with Session() as s:
        _require_creator(s, deployment_id)
    return jsonify(service.list_links(current_app, deployment_id))


@bp.post("/deployments/<int:deployment_id>/share-links")
def create_link(deployment_id: int):
    data = request.get_json(silent=True) or {}
    Session = current_app.extensions["datamason_session_factory"]
    with Session() as s:
        _require_creator(s, deployment_id)
    return jsonify(service.create_link(
        current_app, deployment_id, data.get("role"), data.get("menu_ids"), _uid(),
    )), 201


@bp.delete("/deployments/<int:deployment_id>/share-links/<int:link_id>")
def revoke_link(deployment_id: int, link_id: int):
    Session = current_app.extensions["datamason_session_factory"]
    with Session() as s:
        _require_creator(s, deployment_id)
    return jsonify(service.revoke_link(current_app, deployment_id, link_id, _uid()))


@bp.post("/share/<token>/accept")
def accept(token: str):
    return jsonify(service.accept(current_app, token, _uid()))
