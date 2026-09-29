"""访客公开页路由（M3-5，DR-04）——匿名可访问（/api/v1/public/ 已加入鉴权白名单）。

- GET  /public/{deployment_id}/pages/{page_id}          公开表单元数据（标题 + 字段）
- POST /public/{deployment_id}/pages/{page_id}/submit   匿名提交（写库 + activity_logs）
"""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request
from werkzeug.exceptions import Conflict, HTTPException

from datamason.public import service
from datamason.public.service import PublicError

bp = Blueprint("public", __name__)


@bp.errorhandler(PublicError)
def _public_error(exc: PublicError):
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


@bp.get("/<int:deployment_id>/pages/<int:page_id>")
def get_page(deployment_id: int, page_id: int):
    return jsonify(service.get_page(current_app, deployment_id, page_id))


@bp.post("/<int:deployment_id>/pages/<int:page_id>/submit")
def submit(deployment_id: int, page_id: int):
    return jsonify(service.submit(
        current_app, deployment_id, page_id, request.remote_addr or "",
    )), 201
