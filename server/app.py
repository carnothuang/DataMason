"""DataMason Server 应用工厂。"""
from __future__ import annotations

import os

from flask import Flask, jsonify

from server.config import get_config
from server.models import create_all_lazy


def create_app(config_object: object | None = None) -> Flask:
    """创建 Flask 应用；惰性建表；健康检查就绪。"""
    app = Flask(__name__)
    cfg = get_config(config_object)
    app.config.update(**cfg.as_flask_config())

    create_all_lazy(app)

    @app.get("/api/v1/health")
    def health():
        return jsonify({"status": "ok", "version": app.config["APP_VERSION"]})

    from server.auth.routes import bp as auth_bp

    app.register_blueprint(auth_bp, url_prefix="/api/v1/auth")

    # 元数据驱动 CRUD 蓝图在 M0-4 引入；M0-1 仅健康检查 + 首次启动向导

    return app
