"""部署路由：部署清单 + 部署流（预览提案 / 确认部署）。"""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request
from werkzeug.exceptions import HTTPException

from server.crud import runtime
from server.deploy import service
from server.deploy.service import DeployError

bp = Blueprint("deploy", __name__)


def _body() -> dict:
    return request.get_json(silent=True) or {}


@bp.get("/deployments")
def list_deployments():
    return jsonify(service.list_deployments(current_app))


@bp.post("/deployments/preview")
def preview():
    data = _body()
    description = (data.get("description") or "").strip()
    if not description:
        return jsonify({"error": "请描述你要验证什么（一句话）"}), 400
    try:
        return jsonify(service.preview(current_app, description))
    except DeployError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 502


@bp.post("/deployments/confirm")
def confirm():
    data = _body()
    spec_json = data.get("spec_json")
    biz_name = (data.get("biz_name") or "").strip()
    purpose = (data.get("description") or "").strip()
    if not spec_json or not biz_name:
        return jsonify({"error": "缺少 spec_json 或 biz_name"}), 400
    seed_data = bool(data.get("seed_data", True))
    try:
        result = service.confirm(
            current_app, spec_json, biz_name,
            seed_data=seed_data, purpose=purpose,
        )
        return jsonify(result), 201
    except service.DeployError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 502


# ---------------- M1-3 按部署只读浏览 ----------------

_RESERVED = {"page", "page_size", "sort", "order"}


@bp.get("/deployments/<int:deployment_id>/tables")
def deployment_tables(deployment_id: int):
    try:
        return jsonify(runtime.list_tables(current_app, deployment_id))
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404


@bp.get("/deployments/<int:deployment_id>/tables/<table_name>/rows")
def deployment_rows(deployment_id: int, table_name: str):
    page = max(int(request.args.get("page", 1)), 1)
    page_size = min(max(int(request.args.get("page_size", 20)), 1), 200)
    sort = request.args.get("sort")
    order = request.args.get("order", "asc")
    filters = {k: v for k, v in request.args.items() if k not in _RESERVED}
    try:
        return jsonify(runtime.list_rows(
            current_app, deployment_id, table_name,
            page, page_size, sort, order, filters,
        ))
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 400
