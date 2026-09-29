"""配置中心路由：/api/v1/settings/*。

M1 起：多模型 / 数据连接 / Redis 配置；M2-1 起全部要求平台管理员（app.py 鉴权层）。
"""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from datamason.settings import service

bp = Blueprint("settings", __name__)


def _body() -> dict:
    return request.get_json(silent=True) or {}


def _error(exc: Exception, code: int = 400):
    return jsonify({"error": str(exc)}), code


# ---------- LLM 模型 ----------

@bp.get("/models")
def list_models():
    return jsonify(service.list_models(current_app))


@bp.post("/models")
def create_model():
    data = _body()
    required = ("name", "base_url")
    missing = [k for k in required if not data.get(k)]
    if missing:
        return _error(ValueError(f"缺少必填项: {', '.join(missing)}"))
    try:
        return jsonify(service.create_model(current_app, data)), 201
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@bp.put("/models/<int:model_id>")
def update_model(model_id: int):
    try:
        return jsonify(service.update_model(current_app, model_id, _body()))
    except KeyError as exc:
        return _error(exc, 404)
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@bp.delete("/models/<int:model_id>")
def delete_model(model_id: int):
    try:
        service.delete_model(current_app, model_id)
        return jsonify({"deleted": True})
    except KeyError as exc:
        return _error(exc, 404)


@bp.post("/models/<int:model_id>/default")
def set_default(model_id: int):
    try:
        return jsonify(service.set_default_model(current_app, model_id))
    except KeyError as exc:
        return _error(exc, 404)


@bp.post("/models/<int:model_id>/test")
def test_model(model_id: int):
    try:
        return jsonify(service.test_model(current_app, model_id))
    except KeyError as exc:
        return _error(exc, 404)


# ---------- 数据连接 ----------

@bp.get("/connections")
def list_connections():
    return jsonify(service.list_connections(current_app))


@bp.post("/connections")
def create_connection():
    data = _body()
    dialect = data.get("dialect")
    if not data.get("name"):
        return _error(ValueError("缺少连接名称"))
    if dialect not in ("sqlite", "mysql", "postgresql"):
        return _error(ValueError("dialect 必须是 sqlite/mysql/postgresql"))
    if dialect == "sqlite" and not data.get("db_path"):
        return _error(ValueError("SQLite 需要 db_path（文件名）"))
    try:
        return jsonify(service.create_connection(current_app, data)), 201
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@bp.delete("/connections/<int:conn_id>")
def delete_connection(conn_id: int):
    try:
        service.delete_connection(current_app, conn_id)
        return jsonify({"deleted": True})
    except KeyError as exc:
        return _error(exc, 404)


@bp.post("/connections/<int:conn_id>/test")
def test_connection(conn_id: int):
    try:
        return jsonify(service.test_connection(current_app, conn_id))
    except KeyError as exc:
        return _error(exc, 404)


# ---------- Redis ----------

@bp.get("/redis")
def get_redis():
    return jsonify(service.get_redis_config(current_app))


@bp.put("/redis")
def save_redis():
    try:
        return jsonify(service.save_redis_config(current_app, _body()))
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@bp.post("/redis/test")
def test_redis():
    return jsonify(service.test_redis(current_app))

