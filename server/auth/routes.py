"""认证/向导路由（M0：setup-status + bootstrap，登录留 M1）。"""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify

from server.auth.service import bootstrap_admin

bp = Blueprint("auth", __name__)


@bp.get("/setup-status")
def setup_status():
    """返回是否需要首次启动配置。needs_setup=True 时业务路由应被引导到配置。"""
    Session = current_app.extensions["datamason_session_factory"]
    from server.models import User

    with Session() as session:
        user_count = session.query(User).count()
    needs_setup = user_count == 0
    return jsonify(
        {
            "needs_setup": needs_setup,
            "llm_configured": bool(current_app.config.get("DATAMASON_LLM_BASE_URL")),
            "user_count": user_count,
        }
    )


@bp.post("/bootstrap")
def bootstrap():
    """触发首次启动向导（从环境变量创建管理员）。幂等。"""
    result = bootstrap_admin(current_app)
    return jsonify(result)
