"""问数据路由（M4-1，P3）：POST /app/{id}/qa/ask。

- 登录 + 成员校验；创建人/管理员可用，操作员默认不开放（防绕过「我的记录」数据级约束）；
- 只读门/生成/执行错误 → 400；非运行态 → 409；非成员 → 403；部署不存在 → 404。
"""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request, session
from werkzeug.exceptions import Conflict, HTTPException

from server.members.service import PermissionDenied
from server.qa import service
from server.qa.service import QaError

bp = Blueprint("qa", __name__)


def _uid() -> int:
    return session.get("user_id") or 1


@bp.post("/<int:deployment_id>/qa/ask")
def qa_ask(deployment_id: int):
    data = request.get_json(silent=True) or {}
    return jsonify(service.ask(current_app, deployment_id, data.get("question"), _uid()))


@bp.errorhandler(PermissionDenied)
def _permission_error(exc: PermissionDenied):
    return jsonify({"error": str(exc)}), 403


@bp.errorhandler(Conflict)
def _conflict(exc: Conflict):
    return jsonify({"error": exc.description}), 409


@bp.errorhandler(QaError)
def _qa_error(exc: QaError):
    return jsonify({"error": str(exc)}), 400


@bp.errorhandler(KeyError)
def _key_error(exc: KeyError):
    return jsonify({"error": str(exc)}), 404


@bp.errorhandler(HTTPException)
def _http_error(exc: HTTPException):
    return jsonify({"error": exc.description}), exc.code


@bp.errorhandler(Exception)
def _generic_error(exc: Exception):  # noqa: BLE001
    return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 400
