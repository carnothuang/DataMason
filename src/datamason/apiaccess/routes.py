"""部署级 API key 管理路由（M3-8，DR-11）——创建人独有（DR-14）。

- GET    /deployments/{id}/api-keys                密钥清单（掩码回读）
- POST   /deployments/{id}/api-keys                发放密钥（明文仅此响应回显一次）
- DELETE /deployments/{id}/api-keys/{key_id}       吊销密钥（立即 401）

外部调用走 M2-3 CRUD REST（/app/{id}/api/...）并以 `X-API-Key: dm_...` 头认证（见 app.py 鉴权层）。
"""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request, session
from werkzeug.exceptions import HTTPException

from datamason.apiaccess import service
from datamason.apiaccess.service import ApiKeyError
from datamason.members import service as member_service
from datamason.members.service import PermissionDenied
from datamason.models import Deployment

bp = Blueprint("apiaccess", __name__)


def _uid() -> int:
    return session.get("user_id") or 1


def _require_creator(s, deployment_id: int) -> Deployment:
    d = s.get(Deployment, deployment_id)
    if d is None:
        raise KeyError("部署不存在")
    if d.owner_id != _uid() and member_service.get_role(s, d, _uid()) != "creator":
        raise PermissionDenied("仅创建人可管理 API 密钥（管理员无此项能力）")
    return d


@bp.errorhandler(PermissionDenied)
def _permission_error(exc: PermissionDenied):
    return jsonify({"error": str(exc)}), 403


@bp.errorhandler(ApiKeyError)
def _apikey_error(exc: ApiKeyError):
    return jsonify({"error": str(exc)}), 400


@bp.errorhandler(HTTPException)
def _http_error(exc: HTTPException):
    return jsonify({"error": exc.description}), exc.code


@bp.errorhandler(KeyError)
def _key_error(exc: KeyError):
    return jsonify({"error": str(exc)}), 404


@bp.get("/deployments/<int:deployment_id>/api-keys")
def list_keys(deployment_id: int):
    Session = current_app.extensions["datamason_session_factory"]
    with Session() as s:
        _require_creator(s, deployment_id)
    return jsonify(service.list_keys(current_app, deployment_id))


@bp.post("/deployments/<int:deployment_id>/api-keys")
def create_key(deployment_id: int):
    data = request.get_json(silent=True) or {}
    Session = current_app.extensions["datamason_session_factory"]
    with Session() as s:
        _require_creator(s, deployment_id)
    return jsonify(service.create_key(
        current_app, deployment_id, data.get("name"), _uid(),
    )), 201


@bp.delete("/deployments/<int:deployment_id>/api-keys/<int:key_id>")
def revoke_key(deployment_id: int, key_id: int):
    Session = current_app.extensions["datamason_session_factory"]
    with Session() as s:
        _require_creator(s, deployment_id)
    return jsonify(service.revoke_key(current_app, deployment_id, key_id, _uid()))
