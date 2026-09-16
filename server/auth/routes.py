"""认证/会话/账号管理路由（M2-1：登录 + 轻量会话 + 平台账号管理）。

- 会话：Flask signed cookie（SECRET_KEY 签名），自建轻量，不引 FAB。
- 白名单（无需登录）：setup-status / bootstrap / login / logout / me / health；
  其余 /api/* 由 app.py 的 before_request 统一拦截 401。
- 账号管理（/auth/users*）：登录且平台管理员（app.py 拦截层保证）。
"""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request, session

from server.auth.service import bootstrap_admin, hash_password, verify_password

bp = Blueprint("auth", __name__)


def _payload(user) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name or user.username,
        "is_platform_admin": bool(user.is_platform_admin),
    }


def _load_user(uid: int):
    Session = current_app.extensions["datamason_session_factory"]
    from server.models import User

    with Session() as s:
        return s.get(User, uid)


@bp.get("/setup-status")
def setup_status():
    """返回是否需要首次启动配置。needs_setup=True 时业务路由应被引导到配置。"""
    Session = current_app.extensions["datamason_session_factory"]
    from server.models import User
    from server.settings import service as settings_service

    with Session() as session_db:
        user_count = session_db.query(User).count()
    needs_setup = user_count == 0
    # LLM 配置来源：环境变量（M0）或设置页登记的模型（M1）——两者任一即视为已配置
    llm_configured = bool(current_app.config.get("DATAMASON_LLM_BASE_URL")) or \
        settings_service.has_configured_model(current_app)
    return jsonify(
        {
            "needs_setup": needs_setup,
            "llm_configured": llm_configured,
            "user_count": user_count,
        }
    )


@bp.post("/bootstrap")
def bootstrap():
    """触发首次启动向导（从环境变量创建管理员）。幂等。"""
    result = bootstrap_admin(current_app)
    return jsonify(result)


@bp.post("/setup")
def setup():
    """M1 首次启动向导（界面版）：表单创建首个平台管理员（仅 users 为空时）。"""
    from server.auth.service import SetupError, setup_admin

    data = request.get_json(silent=True) or {}
    try:
        result = setup_admin(
            current_app,
            data.get("username"),
            data.get("password"),
            data.get("display_name") or "",
        )
    except SetupError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result), 201


# ---------------- 登录 / 会话（M2-1） ----------------


@bp.post("/login")
def login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        return jsonify({"error": "请输入用户名和密码"}), 400

    Session = current_app.extensions["datamason_session_factory"]
    from server.models import User

    with Session() as s:
        user = s.query(User).filter_by(username=username).first()
        if user is None or not verify_password(password, user.password_hash):
            return jsonify({"error": "用户名或密码错误"}), 401
        session.clear()
        session["user_id"] = user.id
        return jsonify(_payload(user))


@bp.post("/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})


@bp.get("/me")
def me():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"error": "未登录"}), 401
    user = _load_user(uid)
    if user is None:
        session.clear()
        return jsonify({"error": "未登录"}), 401
    return jsonify(_payload(user))


# ---------------- 账号管理（平台管理员，M2-1） ----------------


@bp.get("/users")
def list_users():
    Session = current_app.extensions["datamason_session_factory"]
    from server.models import User

    with Session() as s:
        users = s.query(User).order_by(User.id).all()
        return jsonify([_payload(u) for u in users])


@bp.post("/users")
def create_user():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        return jsonify({"error": "用户名和密码必填"}), 400

    Session = current_app.extensions["datamason_session_factory"]
    from server.models import User

    with Session() as s:
        if s.query(User).filter_by(username=username).first() is not None:
            return jsonify({"error": f"用户名 {username} 已存在"}), 400
        user = User(
            username=username,
            display_name=(data.get("display_name") or "").strip() or username,
            password_hash=hash_password(password),
            is_platform_admin=bool(data.get("is_platform_admin", False)),
        )
        s.add(user)
        s.commit()
        return jsonify(_payload(user)), 201


@bp.put("/users/<int:user_id>")
def update_user(user_id: int):
    data = request.get_json(silent=True) or {}
    Session = current_app.extensions["datamason_session_factory"]
    from server.models import User

    with Session() as s:
        user = s.get(User, user_id)
        if user is None:
            return jsonify({"error": "用户不存在"}), 404

        if "display_name" in data:
            user.display_name = (data.get("display_name") or "").strip() or user.username
        if data.get("password"):
            user.password_hash = hash_password(data["password"])
        if "is_platform_admin" in data:
            user.is_platform_admin = bool(data["is_platform_admin"])
        s.commit()
        return jsonify(_payload(user))
