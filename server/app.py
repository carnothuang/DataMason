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

    from server.security import Cipher

    app.extensions["cipher"] = Cipher(app.config.get("DATA_ROOT", "./data"))

    @app.get("/api/v1/health")
    def health():
        return jsonify({"status": "ok", "version": app.config["APP_VERSION"]})

    from server.auth.routes import bp as auth_bp
    from server.settings.routes import bp as settings_bp
    from server.deploy.routes import bp as deploy_bp

    app.register_blueprint(auth_bp, url_prefix="/api/v1/auth")
    app.register_blueprint(settings_bp, url_prefix="/api/v1/settings")
    app.register_blueprint(deploy_bp, url_prefix="/api/v1")

    _register_spa(app)

    # 元数据驱动 CRUD 蓝图在 M0-4 引入；M0-1 仅健康检查 + 首次启动向导

    return app


def _register_spa(app: Flask) -> None:
    """生产模式托管前端构建产物 web/dist（单端口）；SPA 路由回退到 index.html。

    开发模式（vite dev server）下 dist 不存在，自动跳过，前端走 5173 代理。
    """
    import os
    from pathlib import Path

    from flask import send_from_directory

    dist = Path(app.root_path).resolve().parent / "web" / "dist"
    assets = dist / "assets"
    if not dist.exists():
        return

    @app.get("/assets/<path:filename>")
    def assets_files(filename):
        return send_from_directory(assets, filename)

    @app.get("/")
    @app.get("/<path:path>")
    def spa_fallback(path: str = ""):
        # 仅回退非 API、非静态资源路径
        if path.startswith("api/"):
            return ("Not Found", 404)
        target = dist / path
        if path and target.is_file():
            return send_from_directory(dist, path)
        return send_from_directory(dist, "index.html")
