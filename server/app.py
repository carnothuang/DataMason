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
    from server.workspace.routes import bp as workspace_bp

    app.register_blueprint(auth_bp, url_prefix="/api/v1/auth")
    app.register_blueprint(settings_bp, url_prefix="/api/v1/settings")
    app.register_blueprint(deploy_bp, url_prefix="/api/v1")
    app.register_blueprint(workspace_bp, url_prefix="/api/v1/app")

    _register_auth_guard(app)
    _register_spa(app)

    # 元数据驱动 CRUD 蓝图在 M0-4 引入；M0-1 仅健康检查 + 首次启动向导

    return app


def _register_auth_guard(app: Flask) -> None:
    """M2-1 统一鉴权：/api/* 需登录（白名单除外）；设置与账号管理需平台管理员。

    - 会话：Flask signed cookie（login 时写 session["user_id"]）。
    - 白名单：健康检查 + 首启向导 + 登录/登出/me（me 未登录返回 401 而非拦截）。
    - SPA/静态资源（非 /api/）不拦截，由前端登录页引导。
    """
    from flask import jsonify, request, session

    public_paths = {
        "/api/v1/health",
        "/api/v1/auth/login",
        "/api/v1/auth/logout",
        "/api/v1/auth/me",
        "/api/v1/auth/setup-status",
        "/api/v1/auth/bootstrap",
    }
    admin_prefixes = ("/api/v1/settings/", "/api/v1/auth/users")

    @app.before_request
    def _auth_guard():
        path = request.path
        if not path.startswith("/api/") or path in public_paths:
            return None
        uid = session.get("user_id")
        if not uid:
            return jsonify({"error": "未登录或会话已失效"}), 401

        if path.startswith(admin_prefixes):
            Session = app.extensions["datamason_session_factory"]
            from server.models import User

            with Session() as s:
                user = s.get(User, uid)
            if user is None or not user.is_platform_admin:
                return jsonify({"error": "需要平台管理员权限"}), 403
        return None


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
