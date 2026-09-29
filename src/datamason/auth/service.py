"""用户/会话/首次启动向导（M0 环境变量版，DR-10）。

M0 语义：
- 首次启动：若 users 为空且环境变量提供管理员账密 → 创建平台管理员。
- 未初始化（无用户且未配置）：/api/v1/auth/setup-status 返回 needs_setup，
  应用层应拦截业务路由并引导配置（M1 起换设置页，M0 用环境变量）。
"""
from __future__ import annotations

import hashlib
import hmac
import os


def hash_password(password: str) -> str:
    """PBKDF2-SHA256，格式 pbkdf2$iterations$salt$hash_hex。"""
    salt = os.urandom(16)
    iterations = 60_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt_hex, hash_hex = stored.split("$")
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


def bootstrap_admin(app) -> dict:
    """首次启动向导（环境变量版）：users 为空时创建管理员。

    返回 {"initialized": bool, "reason": str}。幂等：已有用户则跳过。
    """
    Session = app.extensions["datamason_session_factory"]
    from datamason.models import User

    with Session() as session:
        if session.query(User).count() > 0:
            return {"initialized": False, "reason": "users_not_empty"}

        username = app.config.get("DATAMASON_ADMIN_USERNAME", "")
        password = app.config.get("DATAMASON_ADMIN_PASSWORD", "")
        if not username or not password:
            return {"initialized": False, "reason": "env_missing"}

        user = User(
            username=username,
            display_name=username,
            password_hash=hash_password(password),
            is_platform_admin=True,
        )
        session.add(user)
        session.commit()
        return {"initialized": True, "reason": "admin_created"}


class SetupError(ValueError):
    """首次启动向导错误 → 400。"""


def setup_admin(app, username: str, password: str, display_name: str = "",
                llm: dict | None = None) -> dict:
    """M1 首次启动向导（界面版）：users 为空时按表单创建平台管理员。

    可选 llm={name,base_url,api_key,model_name}：同批登记默认模型（DR-10 向导含 LLM 端点）。
    幂等保护：已有用户则拒绝（避免越权建号）。
    """
    username = (username or "").strip()
    password = password or ""
    if not username or not password:
        raise SetupError("用户名和密码必填")
    if len(password) < 6:
        raise SetupError("密码至少 6 位")

    Session = app.extensions["datamason_session_factory"]
    from datamason.models import User

    with Session() as session:
        if session.query(User).count() > 0:
            raise SetupError("系统已初始化，无需再次设置")
        user = User(
            username=username,
            display_name=(display_name or "").strip() or username,
            password_hash=hash_password(password),
            is_platform_admin=True,
        )
        session.add(user)
        session.commit()
        uid = user.id

    llm_configured = False
    llm = llm or {}
    if (llm.get("base_url") or "").strip() and (llm.get("api_key") or "").strip():
        from datamason.settings import service as settings_service

        m = settings_service.create_model(app, {
            "name": (llm.get("name") or "首次启动配置").strip(),
            "base_url": llm["base_url"].strip(),
            "api_key": llm["api_key"],
            "model_name": (llm.get("model_name") or "").strip(),
            "temperature": 0,
        })
        settings_service.set_default_model(app, m["id"])
        llm_configured = True
    return {"id": uid, "username": username, "llm_configured": llm_configured}
