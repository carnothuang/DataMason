"""部署路由：部署清单 + 部署流（预览提案 / 确认部署）。"""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

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
