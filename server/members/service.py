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
    FlowSpec,
    PageSpec,
    TableSpec,
)

ROLES = ("creator", "admin", "operator")

# 操作动作白名单（0.11.0 M5-C，DR-30）：功能 × 动作
ACTION_KEYS = ("view", "create", "update", "delete", "export", "import")

# 角色默认动作：空配置 actions_json 时按此解释（存量兼容，不改变升级前行为）。
# 操作员默认不含 import（导入为 M5 新增能力，默认关闭，显式授予才可用）。
ROLE_DEFAULT_ACTIONS: dict[str, frozenset[str]] = {
    "creator": frozenset(ACTION_KEYS),
    "admin": frozenset(ACTION_KEYS),
    "operator": frozenset(("view", "create", "update", "delete", "export")),
}

# 角色默认数据范围：空 data_scope 时按此解释（DR-30）
ROLE_DEFAULT_SCOPE = {"creator": "all", "admin": "all", "operator": "own"}


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


def _grant_action_scope(grant) -> tuple[set[str] | None, str | None]:
    """解析一条授权的显式动作/范围；空配置返回 (None, None) 表示按角色默认。"""
    raw = grant.actions or {}
    actions = {k for k, v in raw.items() if v and k in ACTION_KEYS}
    if not actions and not raw:
        return None, None
    scope = grant.data_scope or None
    return actions, scope


def table_grants_map(sess, deployment: Deployment, user_id: int) -> dict[str, list]:
    """把某用户的授权行按业务表名归组（菜单 → 页面 → 表，DR-02）。

    - crud_list/public_form 页面：直接取其 table_id。
    - 流程页面（kind=flow）：授权一个流程功能 = 授权其全部步骤页面背后的表。
    """
    grants = (
        sess.query(DeploymentMenuGrant)
        .filter_by(user_id=user_id)
        .join(DeploymentMenu, DeploymentMenuGrant.menu_id == DeploymentMenu.id)
        .filter(DeploymentMenu.deployment_id == deployment.id)
        .all()
    )
    if not grants:
        return {}
    menus = {
        m.id: m
        for m in sess.query(DeploymentMenu).filter_by(deployment_id=deployment.id).all()
    }
    pages = {
        p.id: p
        for p in sess.query(PageSpec).filter_by(deployment_id=deployment.id).all()
    }
    flow_pages: dict[int, list[int]] = {}
    for f in sess.query(FlowSpec).filter_by(deployment_id=deployment.id).all():
        flow_pages[f.id] = [
            st.get("page_id") for st in f.steps if st.get("page_id")
        ]
    specs = {
        t.id: t.name
        for t in sess.query(TableSpec).filter_by(deployment_id=deployment.id).all()
    }

    def _tables_for_page(page) -> set[str]:
        names: set[str] = set()
        if page.table_id is not None and page.table_id in specs:
            names.add(specs[page.table_id])
        if page.kind == "flow" and page.flow_id in flow_pages:
            for step_page_id in flow_pages[page.flow_id]:
                step_page = pages.get(step_page_id)
                if step_page and step_page.table_id in specs:
                    names.add(specs[step_page.table_id])
        return names

    out: dict[str, list] = {}
    for g in grants:
        menu = menus.get(g.menu_id)
        page = pages.get(menu.ref_id) if menu and menu.ref_id else None
        if page is None:
            continue
        for name in _tables_for_page(page):
            out.setdefault(name, []).append(g)
    return out


def table_access_map(sess, deployment: Deployment, user_id: int) -> dict[str, dict]:
    """某用户对每张业务表的动作集合与数据范围（0.11.0 M5-C 统一解析，DR-30）。

    - 创建人/管理员：全动作 + all（沿用 DR-14 的部署级边界，不在此表达工程/停止/销毁）。
    - 操作员：按其被授权功能归并；空配置 = 角色默认；数据范围「取最宽」（任一 all 则 all）。
    - 无 view 动作的表不返回（等同于不可见）。
    """
    specs = sess.query(TableSpec).filter_by(deployment_id=deployment.id).all()
    role = get_role(sess, deployment, user_id)
    if role in ("creator", "admin"):
        return {
            t.name: {"actions": set(ACTION_KEYS), "data_scope": "all"} for t in specs
        }
    if role is None:
        return {}
    grants = table_grants_map(sess, deployment, user_id)
    out: dict[str, dict] = {}
    for name, group in grants.items():
        actions: set[str] = set()
        scope = "own"
        for g in group:
            explicit, sc = _grant_action_scope(g)
            actions |= explicit if explicit is not None else set(ROLE_DEFAULT_ACTIONS["operator"])
            if sc == "all":
                scope = "all"
        if "view" in actions:
            out[name] = {"actions": actions, "data_scope": scope}
    return out


def resolve_access(sess, deployment: Deployment, user_id: int,
                   table_name: str | None = None, page_id: int | None = None) -> dict:
    """统一权限解析（0.11.0 M5-C1，DR-30）。

    返回 {role, actions:set, data_scope:'all'|'own', writable:bool}。
    page_id 可替代 table_name（流程页/报表页 → 其背后表）。
    """
    role = get_role(sess, deployment, user_id)
    if role is None:
        return {"role": None, "actions": set(), "data_scope": "own", "writable": False}
    if table_name is None and page_id is not None:
        page = sess.get(PageSpec, page_id)
        if page is not None and page.deployment_id == deployment.id and page.table_id:
            ts = sess.get(TableSpec, page.table_id)
            table_name = ts.name if ts is not None else None
    if role in ("creator", "admin"):
        return {
            "role": role, "actions": set(ROLE_DEFAULT_ACTIONS[role]),
            "data_scope": ROLE_DEFAULT_SCOPE[role], "writable": True,
        }
    access = table_access_map(sess, deployment, user_id).get(table_name or "", None)
    if access is None:
        return {"role": role, "actions": set(), "data_scope": "own", "writable": False}
    actions = set(access["actions"])
    return {
        "role": role, "actions": actions, "data_scope": access["data_scope"],
        "writable": bool(actions & {"create", "update", "delete"}),
    }


def assert_action(sess, deployment: Deployment, user_id: int, table_name: str,
                  action: str, page_id: int | None = None) -> dict:
    """动作校验：无权限抛 PermissionDenied → 403。返回访问解析结果。"""
    access = resolve_access(sess, deployment, user_id, table_name=table_name, page_id=page_id)
    if action not in access["actions"]:
        raise PermissionDenied("你没有该操作的权限")
    return access


def allowed_table_names(sess, deployment: Deployment, user_id: int) -> list[str]:
    """操作员可操作的业务表 = 被授权菜单所指向页面背后的表（需含 view 动作）；创建人/管理员全量。"""
    specs = (
        sess.query(TableSpec)
        .filter_by(deployment_id=deployment.id)
        .all()
    )
    role = get_role(sess, deployment, user_id)
    if role in ("creator", "admin"):
        return [t.name for t in specs]
    access = table_access_map(sess, deployment, user_id)
    return [t.name for t in specs if t.name in access]