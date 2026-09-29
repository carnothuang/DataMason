"""分享链接（M3-5，DR-04）：创建人生成链接 → 对方进入即绑定为该部署成员。

- 分享=创建人独有（DR-14）；链接携带档位（admin/operator）与操作员的功能菜单集合。
- 「绑定身份」扩展 M2-1 会话体系：接收方需先登录，进入链接即按链接档位加入成员
  （替换 MVP「成员代录」话术——不再由成员代录/替对方操作）。
"""
from __future__ import annotations

import json
import secrets

from datamason.audit.service import log_system
from datamason.members import service as member_service
from datamason.models import (
    Deployment,
    DeploymentMember,
    DeploymentMenu,
    DeploymentMenuGrant,
    DeploymentShareToken,
    PageSpec,
    User,
)

ROLE_LABEL = {"creator": "创建人", "admin": "管理员", "operator": "操作员"}


class ShareError(ValueError):
    """分享链接校验错误 → 400。"""


def _session(app):
    return app.extensions["datamason_session_factory"]()


def _deployment(s, deployment_id: int) -> Deployment:
    d = s.get(Deployment, deployment_id)
    if d is None:
        raise KeyError("部署不存在")
    return d


def _menu_rows(s, deployment_id: int) -> list[dict]:
    menus = (
        s.query(DeploymentMenu)
        .filter_by(deployment_id=deployment_id)
        .order_by(DeploymentMenu.order_index, DeploymentMenu.id)
        .all()
    )
    return [{"id": m.id, "label": m.label, "parent_id": m.parent_id} for m in menus]


def _public_pages(s, deployment_id: int) -> list[dict]:
    pages = (
        s.query(PageSpec)
        .filter_by(deployment_id=deployment_id, access="public", kind="public_form")
        .order_by(PageSpec.id)
        .all()
    )
    return [{"id": p.id, "title": p.title} for p in pages]


def _validate_menu_ids(s, deployment_id: int, menu_ids) -> list[int]:
    if not isinstance(menu_ids, list) or not menu_ids:
        raise ShareError("操作员分享至少需要分配一个功能菜单")
    valid = {
        mid for (mid,) in s.query(DeploymentMenu.id)
        .filter_by(deployment_id=deployment_id).all()
    }
    if not set(menu_ids).issubset(valid):
        raise ShareError("包含不属于该部署的菜单")
    return list(dict.fromkeys(menu_ids))


def create_link(app, deployment_id: int, role: str, menu_ids, actor_user_id: int) -> dict:
    if role not in ("admin", "operator"):
        raise ShareError("分享档位仅支持 admin/operator")
    with _session(app) as s:
        _deployment(s, deployment_id)
        valid_ids: list[int] = []
        if role == "operator":
            valid_ids = _validate_menu_ids(s, deployment_id, menu_ids)
        row = DeploymentShareToken(
            deployment_id=deployment_id,
            token=secrets.token_urlsafe(24),
            role=role,
            menu_ids_json=json.dumps(valid_ids, ensure_ascii=False),
            created_by=actor_user_id,
        )
        s.add(row)
        s.flush()
        link_id = row.id
        token = row.token
        s.commit()
    log_system(
        app, deployment_id, "member", actor_user_id=actor_user_id,
        object_kind="share", object_pk=str(link_id),
        object_label=f"生成分享链接（{ROLE_LABEL.get(role, role)}）",
        changes={"type": "share_create", "role": role, "menu_ids": valid_ids},
    )
    return {"id": link_id, "token": token, "role": role, "menu_ids": valid_ids}


def list_links(app, deployment_id: int) -> dict:
    with _session(app) as s:
        _deployment(s, deployment_id)
        rows = (
            s.query(DeploymentShareToken)
            .filter_by(deployment_id=deployment_id)
            .order_by(DeploymentShareToken.id.desc())
            .all()
        )
        users = {u.id: (u.display_name or u.username) for u in s.query(User).all()}
        return {
            "links": [
                {
                    "id": r.id,
                    "token": r.token,
                    "role": r.role,
                    "menu_ids": r.menu_ids,
                    "created_by": r.created_by,
                    "created_by_display": users.get(r.created_by, f"#{r.created_by}"),
                    "created_at": r.created_at.isoformat(sep=" ") if r.created_at else "",
                }
                for r in rows
            ],
            "menus": _menu_rows(s, deployment_id),
            "public_pages": _public_pages(s, deployment_id),
        }


def revoke_link(app, deployment_id: int, link_id: int, actor_user_id: int) -> dict:
    with _session(app) as s:
        _deployment(s, deployment_id)
        row = s.get(DeploymentShareToken, link_id)
        if row is None or row.deployment_id != deployment_id:
            raise KeyError("分享链接不存在")
        role = row.role
        s.delete(row)
        s.commit()
    log_system(
        app, deployment_id, "member", actor_user_id=actor_user_id,
        object_kind="share", object_pk=str(link_id),
        object_label=f"撤销分享链接（{ROLE_LABEL.get(role, role)}）",
        changes={"type": "share_revoke", "role": role},
    )
    return {"ok": True}


def _set_grants(s, deployment_id: int, user_id: int, menu_ids: list[int]) -> None:
    """按人授权（DR-02）：替换为该链接绑定的功能集合（按部署隔离）。"""
    owned_menu_ids = [
        mid
        for (mid,) in s.query(DeploymentMenu.id)
        .filter_by(deployment_id=deployment_id)
        .all()
    ]
    if owned_menu_ids:
        s.query(DeploymentMenuGrant).filter(
            DeploymentMenuGrant.user_id == user_id,
            DeploymentMenuGrant.menu_id.in_(owned_menu_ids),
        ).delete(synchronize_session=False)
    for menu_id in menu_ids:
        s.add(DeploymentMenuGrant(menu_id=menu_id, user_id=user_id))


def accept(app, token: str, uid: int) -> dict:
    token = (token or "").strip()
    with _session(app) as s:
        row = s.query(DeploymentShareToken).filter_by(token=token).first()
        if row is None:
            raise KeyError("分享链接无效或已撤销")
        d = _deployment(s, row.deployment_id)
        deployment_id = d.id
        role = member_service.get_role(s, d, uid)
        if role != "creator":  # 创建人不受链接影响
            member = (
                s.query(DeploymentMember)
                .filter_by(deployment_id=deployment_id, user_id=uid)
                .first()
            )
            if member is None:
                s.add(DeploymentMember(
                    deployment_id=deployment_id, user_id=uid, role=row.role,
                ))
            else:
                member.role = row.role
            if row.role == "operator":
                _set_grants(s, deployment_id, uid, row.menu_ids)
            else:
                # 管理员不需要按人授权；仅清本部署残留授权（跨部署隔离）
                _set_grants(s, deployment_id, uid, [])
            s.commit()
        user = s.get(User, uid)
        who = (user.display_name or user.username) if user else f"#{uid}"
        entry_path = d.entry_path or f"/app/{deployment_id}"
        biz_name = d.biz_name
        bound_role = member_service.get_role(s, d, uid)
    log_system(
        app, deployment_id, "member", actor_user_id=uid,
        object_kind="member", object_pk=str(uid),
        object_label=f"{who} 通过分享链接加入（{ROLE_LABEL.get(bound_role, bound_role)}）",
        changes={"type": "share_accept", "role": bound_role, "share_token": token[:8]},
    )
    return {
        "deployment_id": deployment_id,
        "entry_path": entry_path,
        "biz_name": biz_name,
        "role": bound_role,
    }
