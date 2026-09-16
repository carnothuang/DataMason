"""成员与权限（M2-5）：三层角色 + 按人授权（DR-02）+ 创建人独有变更（DR-14）。

角色只定档位（设计细化 §十一 七）：
- creator 创建人：一切（改模型/改界面/配权限/加成员/停止/销毁）
- admin 管理员：全套业务功能 + 数据管理 + 查看成员与日志；
  不含工程室、不含停止/销毁/分享、成员增删与授权变更是创建人独有（DR-14）
- operator 操作员：只见被授权菜单（deployment_menu_grants）+「我的记录」
数据级：操作员默认只见 created_by = 当前会话用户的记录（归属列 DR-05）。
"""
from __future__ import annotations

from server.models import (
    Deployment,
    DeploymentMember,
    DeploymentMenu,
    DeploymentMenuGrant,
    PageSpec,
    TableSpec,
)

ROLES = ("creator", "admin", "operator")


class PermissionDenied(PermissionError):
    pass


def get_role(sess, deployment: Deployment, user_id: int) -> str | None:
    """返回用户在该部署的角色（creator/admin/operator）；非成员返回 None。

    创建人 = deployment.owner_id，即使 deployment_members 缺行（历史部署）也按创建人算。
    """
    if deployment.owner_id == user_id:
        return "creator"
    row = (
        sess.query(DeploymentMember)
        .filter_by(deployment_id=deployment.id, user_id=user_id)
        .first()
    )
    return row.role if row is not None else None


def assert_member(sess, deployment: Deployment, user_id: int) -> str:
    """成员校验：非成员抛 PermissionDenied → 路由层映射 403。返回角色。"""
    role = get_role(sess, deployment, user_id)
    if role is None:
        raise PermissionDenied("你不是该部署的成员，无法访问")
    return role


def require_role(sess, deployment: Deployment, user_id: int, roles: tuple[str, ...]) -> str:
    """成员 + 指定角色档位校验；不满足抛 PermissionDenied → 403。"""
    role = assert_member(sess, deployment, user_id)
    if role not in roles:
        raise PermissionDenied("你没有该操作所需权限")
    return role


def granted_menu_ids(sess, deployment: Deployment, user_id: int) -> set[int]:
    rows = (
        sess.query(DeploymentMenuGrant)
        .filter_by(user_id=user_id)
        .join(DeploymentMenu, DeploymentMenuGrant.menu_id == DeploymentMenu.id)
        .filter(DeploymentMenu.deployment_id == deployment.id)
        .all()
    )
    return {g.menu_id for g in rows}


def visible_menu_ids(menus: list[DeploymentMenu], granted: set[int]) -> set[int]:
    """按人授权后可见的菜单 id 集合（平铺菜单输入，含父子）。

    规则（DR-02）：授权的是「功能给某个人」——@granted 的菜单及其整棵子树可见；
    未授权但子孙可见的父菜单保留（它是导航分组，不是功能本身）。
    """
    children: dict[int | None, list[int]] = {}
    for m in menus:
        children.setdefault(m.parent_id, []).append(m.id)

    included: set[int] = set()

    def _include_all(mid: int) -> None:
        included.add(mid)
        for child in children.get(mid, []):
            _include_all(child)

    def _walk(mid: int) -> bool:
        if mid in included:
            return True
        if mid in granted:
            _include_all(mid)
            return True
        has_visible_child = any(_walk(c) for c in children.get(mid, []))
        if has_visible_child:
            included.add(mid)
        return has_visible_child

    for m in menus:
        if m.parent_id is None:
            _walk(m.id)
    return included


def allowed_table_names(sess, deployment: Deployment, user_id: int) -> list[str]:
    """操作员可操作的业务表 = 被授权菜单所指向页面背后的表；创建人/管理员全量。"""
    specs = (
        sess.query(TableSpec)
        .filter_by(deployment_id=deployment.id)
        .all()
    )
    all_names = [t.name for t in specs]
    role = get_role(sess, deployment, user_id)
    if role in ("creator", "admin"):
        return all_names

    granted = granted_menu_ids(sess, deployment, user_id)
    if not granted:
        return []
    menus = (
        sess.query(DeploymentMenu)
        .filter_by(deployment_id=deployment.id)
        .all()
    )
    ref_ids = {m.ref_id for m in menus if m.id in granted and m.ref_id}
    if not ref_ids:
        return []
    pages = (
        sess.query(PageSpec)
        .filter(PageSpec.deployment_id == deployment.id, PageSpec.id.in_(ref_ids))
        .all()
    )
    table_ids = {p.table_id for p in pages if p.table_id}
    if not table_ids:
        return []
    return [t.name for t in specs if t.id in table_ids]