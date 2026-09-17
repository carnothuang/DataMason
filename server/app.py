"""DataMason Server 应用工厂。"""
from __future__ import annotations

import os
from datetime import date, datetime

from flask import Flask, jsonify
from flask.json.provider import DefaultJSONProvider

from server.config import get_config
from server.models import create_all_lazy


class DatamasonJSONProvider(DefaultJSONProvider):
    """业务行含 datetime：序列化为 'YYYY-MM-DD HH:MM:SS[.ffffff]'。

    Flask 3 默认把 datetime 序列化成 RFC822（'Tue, 15 Sep 2026 ... GMT'），
    既丑又无法回填表单；统一改成 isoformat(空格分隔)，前端可直接编辑提交。
    """

    def default(self, o: object) -> object:
        if isinstance(o, datetime):
            return o.isoformat(sep=" ")
        if isinstance(o, date):
            return o.isoformat()
        return super().default(o)


def create_app(config_object: object | None = None) -> Flask:
    """创建 Flask 应用；惰性建表；健康检查就绪。"""
    app = Flask(__name__)
    cfg = get_config(config_object)
    app.config.update(**cfg.as_flask_config())
    app.json = DatamasonJSONProvider(app)

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
    from server.members.routes import bp as members_bp
    from server.flow.routes import bp as flow_bp
    from server.public.routes import bp as public_bp
    from server.share.routes import bp as share_bp
    from server.files.routes import bp as files_bp
    from server.imports.routes import bp as imports_bp
    from server.apiaccess.routes import bp as apiaccess_bp
    from server.qa.routes import bp as qa_bp, engineer_bp as qa_engineer_bp
    from server.ai.routes import settings_bp as ai_settings_bp, deploy_bp as ai_deploy_bp

    app.register_blueprint(auth_bp, url_prefix="/api/v1/auth")
    app.register_blueprint(settings_bp, url_prefix="/api/v1/settings")
    app.register_blueprint(ai_settings_bp, url_prefix="/api/v1/settings")
    app.register_blueprint(deploy_bp, url_prefix="/api/v1")
    app.register_blueprint(members_bp, url_prefix="/api/v1")
    app.register_blueprint(workspace_bp, url_prefix="/api/v1/app")
    app.register_blueprint(qa_bp, url_prefix="/api/v1/app")
    app.register_blueprint(qa_engineer_bp, url_prefix="/api/v1")
    app.register_blueprint(ai_deploy_bp, url_prefix="/api/v1")
    app.register_blueprint(flow_bp, url_prefix="/api/v1")
    app.register_blueprint(share_bp, url_prefix="/api/v1")
    app.register_blueprint(public_bp, url_prefix="/api/v1/public")
    app.register_blueprint(files_bp, url_prefix="/api/v1/app")
    app.register_blueprint(imports_bp, url_prefix="/api/v1/app")
    app.register_blueprint(apiaccess_bp, url_prefix="/api/v1")

    _register_auth_guard(app)
    _register_spa(app)

    # M2-6/DR-03：启动对账任务（后台线程收尾进程崩溃遗留的 pending 日志）
    from server.audit.service import reconcile_pending_background

    reconcile_pending_background(app)

    # 元数据驱动 CRUD 蓝图在 M0-4 引入；M0-1 仅健康检查 + 首次启动向导

    return app


def _is_initialized(app: Flask) -> bool:
    """系统是否已初始化（存在至少一个用户）。结果缓存于 config，避免每请求查库。"""
    if app.config.get("_dm_initialized"):
        return True
    try:
        from server.models import User

        Session = app.extensions["datamason_session_factory"]
        with Session() as s:
            if s.query(User).count() > 0:
                app.config["_dm_initialized"] = True
                return True
    except Exception:  # noqa: BLE001 探测失败时不拦截，避免误伤
        return True
    return False


def _register_auth_guard(app: Flask) -> None:
    """M2-1 统一鉴权：/api/* 需登录（白名单除外）；设置与账号管理需平台管理员。

    - 会话：Flask signed cookie（login 时写 session["user_id"]）。
    - 白名单：健康检查 + 首启向导 + 登录/登出/me（me 未登录返回 401 而非拦截）。
    - SPA/静态资源（非 /api/）不拦截，由前端登录页引导。
    """
    from flask import g, jsonify, request, session

    public_paths = {
        "/api/v1/health",
        "/api/v1/auth/login",
        "/api/v1/auth/logout",
        "/api/v1/auth/me",
        "/api/v1/auth/setup-status",
        "/api/v1/auth/bootstrap",
        "/api/v1/auth/setup",
    }
    admin_prefixes = ("/api/v1/settings/", "/api/v1/auth/users")

    def _extract_api_key() -> str | None:
        """支持 `X-API-Key: dm_...` 或 `Authorization: Bearer dm_...`。"""
        raw = (request.headers.get("X-API-Key") or "").strip()
        if raw:
            return raw
        auth = (request.headers.get("Authorization") or "").strip()
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return None

    @app.before_request
    def _auth_guard():
        path = request.path
        if not path.startswith("/api/") or path in public_paths:
            return None
        # M3-5 访客公开页（DR-04）：匿名提交/读取公开表单，不走登录拦截
        if path.startswith("/api/v1/public/"):
            return None
        # M0-1 首次启动拦截：系统尚未初始化（无任何用户）时，业务 API 一律引导到配置
        if not _is_initialized(app):
            return jsonify({"error": "系统尚未初始化，请先创建管理员账号",
                            "needs_setup": True}), 409
        # M3-8 API key 通道（DR-11）：仅开放该部署的 CRUD REST（/app/{id}/api/tables/*、/api/meta）
        raw_key = _extract_api_key()
        if raw_key:
            from server.apiaccess import service as apikey_service

            ctx = apikey_service.authenticate(app, raw_key)
            if ctx is None:
                return jsonify({"error": "API key 无效或已吊销"}), 401
            allowed = (
                path.startswith(f"/api/v1/app/{ctx['deployment_id']}/api/tables/")
                or path == f"/api/v1/app/{ctx['deployment_id']}/api/meta"
            )
            if not allowed:
                return jsonify({"error": "API key 仅可用于该部署的数据接口"}), 403
            g.api_key = ctx
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
