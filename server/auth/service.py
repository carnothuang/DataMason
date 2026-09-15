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
    from server.models import User

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
