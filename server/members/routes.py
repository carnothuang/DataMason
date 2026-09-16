"""成员与权限路由（M2-5）。

- GET/POST/PUT/DELETE  /deployments/<id>/members*：创建人独有增删与授权变更（DR-14）；
  管理员仅可查看名单（DR-14：管理员=查看成员）；操作员无成员入口。
- GET /deployments/<id>/members/candidates：可选候选账号（全局用户中未加入本部署的）。
- 授权按人（DR-02）：menu_ids 写入 deployment_menu_grants(menu_id, user_id)。
- M2-6：成员增删/角色/授权变更写 system 动作日志（action=member，DR-03）。
"""
from __future__ import annotations

from flask import Blueprint, abort, current_app, jsonify, request, session
from werkzeug.exceptions import HTTPException

from server.audit.service import log_system
from server.members import service
from server.members.service import PermissionDenied
from server.models import (
    Deployment,
    DeploymentMember,
    DeploymentMenu,
    DeploymentMenuGrant,
    User,
)

ROLE_LABEL = {"creator": "创建人", "admin": "管理员", "operator": "操作员"}

bp = Blueprint("members", __name__)


def _body() -> dict:
    return request.get_json(silent=True) or {}


def _menu_labels(s, deployment_id: int, menu_ids: list[int]) -> list[str]:
    if not menu_ids:
        return []
    rows = (
        s.query(DeploymentMenu.label)
        .filter(DeploymentMenu.deployment_id == deployment_id,
                DeploymentMenu.id.in_(menu_ids))
        .all()
    )
    return [r[0] for r in rows]


@bp.errorhandler(HTTPException)
def _http_error(exc: HTTPException):
    return jsonify({"error": exc.description}), exc.code


@bp.errorhandler(PermissionDenied)
def _permission_error(exc: PermissionDenied):
    return jsonify({"error": str(exc)}), 403


def _load_deployment(s, deployment_id: int) -> Deployment:
    d = s.get(Deployment, deployment_id)
    if d is None:
        abort(404, description="部署不存在")
    return d


def _menu_rows(s, deployment_id: int) -> list[dict]:
    menus = (
        s.query(DeploymentMenu)
        .filter_by(deployment_id=deployment_id)
        .order_by(DeploymentMenu.order_index, DeploymentMenu.id)
        .all()
    )
    return [
        {"id": m.id, "label": m.label, "parent_id": m.parent_id}
        for m in menus
    ]


@bp.get("/deployments/<int:deployment_id>/members")
def list_members(deployment_id: int):
    Session = current_app.extensions["datamason_session_factory"]
    with Session() as s:
        d = _load_deployment(s, deployment_id)
        uid = session["user_id"]
        role = service.require_role(s, d, uid, ("creator", "admin"))

        menus = _menu_rows(s, deployment_id)
        all_menu_ids = {m["id"] for m in menus}
        rows = (
            s.query(DeploymentMember, User)
            .join(User, DeploymentMember.user_id == User.id)
            .filter(DeploymentMember.deployment_id == deployment_id)
            .all()
        )
        users = {u.id: u for _, u in rows}
        # 历史部署缺创建人行：补上，保证创建人永远在名单里（即使 owner 角色行缺失）
        if d.owner_id not in users:
            owner = s.get(User, d.owner_id)
            if owner is not None:
                users[owner.id] = owner
                s.add(DeploymentMember(
                    deployment_id=deployment_id, user_id=owner.id, role="creator",
                ))
                s.commit()
        grants = {
            (g.menu_id, g.user_id)
            for g in s.query(DeploymentMenuGrant)
            .join(DeploymentMenu, DeploymentMenuGrant.menu_id == DeploymentMenu.id)
            .filter(DeploymentMenu.deployment_id == deployment_id)
            .all()
        }
        members = []
        for user in users.values():
            m_role = service.get_role(s, d, user.id)
            if m_role == "creator":
                menu_ids = sorted(all_menu_ids)
            elif m_role == "admin":
                menu_ids = sorted(all_menu_ids)
            else:
                menu_ids = sorted(menu_id for menu_id, userId in grants if userId == user.id)
            members.append({
                "user_id": user.id,
                "username": user.username,
                "display_name": user.display_name or user.username,
                "role": m_role,
                "menu_ids": menu_ids,
                "is_self": user.id == uid,
            })
        members.sort(key=lambda m: (0 if m["role"] == "creator" else 1, m["user_id"]))
        return jsonify({"members": members, "menus": menus})


@bp.get("/deployments/<int:deployment_id>/members/candidates")
def list_candidates(deployment_id: int):
    Session = current_app.extensions["datamason_session_factory"]
    with Session() as s:
        d = _load_deployment(s, deployment_id)
        uid = session["user_id"]
        service.require_role(s, d, uid, ("creator", "admin"))

        member_ids = {
            row.user_id
            for row in s.query(DeploymentMember)
            .filter_by(deployment_id=deployment_id)
            .all()
        }
        member_ids.add(d.owner_id)
        users = s.query(User).filter(~User.id.in_(member_ids or {-1})).order_by(User.id).all()
        return jsonify([
            {"id": u.id, "username": u.username,
             "display_name": u.display_name or u.username}
            for u in users
        ])


def _validate_menu_ids(s, deployment_id: int, menu_ids) -> list[int]:
    if not isinstance(menu_ids, list) or not menu_ids:
        abort(400, description="操作员至少需要分配一个功能菜单")
    valid = {
        mid
        for (mid,) in s.query(DeploymentMenu.id)
        .filter_by(deployment_id=deployment_id)
        .all()
    }
    if not set(menu_ids).issubset(valid):
        abort(400, description="包含不属于该部署的菜单")
    return list(dict.fromkeys(menu_ids))


def _set_grants(s, deployment_id: int, user_id: int, menu_ids: list[int]) -> None:
    if menu_ids is None:
        return
    s.query(DeploymentMenuGrant).filter_by(user_id=user_id).delete()
    for menu_id in menu_ids:
        s.add(DeploymentMenuGrant(menu_id=menu_id, user_id=user_id))


@bp.post("/deployments/<int:deployment_id>/members")
def add_member(deployment_id: int):
    data = _body()
    username = (data.get("username") or "").strip()
    role = data.get("role") or ""
    Session = current_app.extensions["datamason_session_factory"]
    with Session() as s:
        d = _load_deployment(s, deployment_id)
        uid = session["user_id"]
        service.require_role(s, d, uid, ("creator",))
        if role not in ("admin", "operator"):
            abort(400, description="角色仅支持 admin/operator")
        user = s.query(User).filter_by(username=username).first()
        if user is None:
            abort(400, description=f"账号 {username} 不存在")
        if service.get_role(s, d, user.id) is not None:
            abort(400, description=f"{user.username} 已是该部署成员")
        menu_ids = []
        if role == "operator":
            menu_ids = _validate_menu_ids(s, deployment_id, data.get("menu_ids"))
        s.add(DeploymentMember(
            deployment_id=deployment_id, user_id=user.id, role=role,
        ))
        if role == "operator":
            _set_grants(s, deployment_id, user.id, menu_ids)
        s.commit()
        log_system(current_app, deployment_id, "member", actor_user_id=uid,
                   object_kind="member", object_pk=str(user.id),
                   object_label=f"{user.username} 加入（{ROLE_LABEL.get(role, role)}）",
                   changes={"type": "add", "role": role,
                            "grants": _menu_labels(s, deployment_id, menu_ids)})
        return jsonify({"ok": True}), 201


@bp.put("/deployments/<int:deployment_id>/members/<int:user_id>")
def update_member(deployment_id: int, user_id: int):
    """改角色 + 授权变更；创建人独有（DR-14）。admin→operator 改格时须带 menu_ids。"""
    data = _body()
    Session = current_app.extensions["datamason_session_factory"]
    with Session() as s:
        d = _load_deployment(s, deployment_id)
        uid = session["user_id"]
        service.require_role(s, d, uid, ("creator",))

        if d.owner_id == user_id:
            abort(400, description="不能修改创建人的角色或授权")
        row = (
            s.query(DeploymentMember)
            .filter_by(deployment_id=deployment_id, user_id=user_id)
            .first()
        )
        if row is None:
            abort(404, description="该成员不在本部署中")

        role = data.get("role")
        old_role = row.role
        if role is not None:
            if role not in ("admin", "operator"):
                abort(400, description="角色仅支持 admin/operator")
            row.role = role

        changed: dict = {"type": "update"}
        if role is not None:
            changed["old_role"] = old_role
            changed["new_role"] = row.role
        if row.role == "operator":
            menu_ids = _validate_menu_ids(s, deployment_id, data.get("menu_ids"))
            changed["grants"] = _menu_labels(s, deployment_id, menu_ids)
            _set_grants(s, deployment_id, user_id, menu_ids)
        s.commit()
        user = s.get(User, user_id)
        log_system(current_app, deployment_id, "member", actor_user_id=uid,
                   object_kind="member", object_pk=str(user_id),
                   object_label=f"{(user.display_name or user.username) if user else user_id} 成员变更",
                   changes=changed)
        return jsonify({"ok": True})


@bp.delete("/deployments/<int:deployment_id>/members/<int:user_id>")
def remove_member(deployment_id: int, user_id: int):
    Session = current_app.extensions["datamason_session_factory"]
    with Session() as s:
        d = _load_deployment(s, deployment_id)
        uid = session["user_id"]
        service.require_role(s, d, uid, ("creator",))
        if d.owner_id == user_id:
            abort(400, description="不能移除创建人")
        if user_id == uid:
            abort(400, description="不能移除自己")
        row = (
            s.query(DeploymentMember)
            .filter_by(deployment_id=deployment_id, user_id=user_id)
            .first()
        )
        if row is None:
            abort(404, description="该成员不在本部署中")
        s.delete(row)
        s.query(DeploymentMenuGrant).filter_by(user_id=user_id).delete()
        s.commit()
        user = s.get(User, user_id)
        log_system(current_app, deployment_id, "member", actor_user_id=uid,
                   object_kind="member", object_pk=str(user_id),
                   object_label=f"{(user.display_name or user.username) if user else user_id} 移除成员",
                   changes={"type": "remove"})
        return jsonify({"ok": True})