"""M2-5 验收：成员与权限（三层角色 + 按人授权 DR-02 + 创建人独有变更 DR-14 + 我的记录）。

- 部署后创建人自动成为 creator 成员；非成员访问工作台/成员接口/CRUD → 403
- 操作员只见被授权菜单与页面（同角色两人菜单不同）与「我的记录」（created_by=自己）
- 管理员全套业务功能（全菜单/全数据）但无工程室、不可改成员；仅可查看成员
- 成员增删与授权变更=创建人独有；操作员增删成员 → 403
- 归属列（DR-05）禁止客户端改写
"""
from __future__ import annotations

import pytest

from server.auth.service import hash_password
from server.models import DeploymentMenu, DeploymentMember, PageSpec, User

LINKED_SPEC = {
    "biz_notes": "进销存：客户与订单",
    "tables": [
        {
            "name": "customers",
            "biz_name": "客户",
            "columns": [
                {"name": "id", "biz_name": "主键", "type": "INTEGER",
                 "is_pk": True, "autoincrement": True, "nullable": False},
                {"name": "name", "biz_name": "客户名", "type": "VARCHAR",
                 "length": 64, "nullable": False},
                {"name": "phone", "biz_name": "电话", "type": "VARCHAR", "length": 32},
            ],
            "indexes": [],
        },
        {
            "name": "orders",
            "biz_name": "订单",
            "columns": [
                {"name": "id", "biz_name": "主键", "type": "INTEGER",
                 "is_pk": True, "autoincrement": True, "nullable": False},
                {"name": "customer_id", "biz_name": "客户", "type": "INTEGER",
                 "nullable": False, "is_fk": True,
                 "ref_table": "customers", "ref_column": "id"},
                {"name": "amount", "biz_name": "金额", "type": "INTEGER"},
            ],
            "indexes": [],
        },
    ],
}


class FakeLlm:
    def __init__(self, payload):
        self.payload = payload

    def chat_json(self, system_prompt, user_prompt):
        class _Usage:
            tokens_in = 1
            tokens_out = 1
            duration_ms = 1

        return self.payload, _Usage()


@pytest.fixture()
def patch_llm(monkeypatch):
    monkeypatch.setattr(
        "server.settings.service.get_default_llm_client",
        lambda _app: FakeLlm(LINKED_SPEC),
    )
    yield


@pytest.fixture()
def env(app):
    """创建平台管理员并登录，返回共享 client 与 id。"""
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        u = User(username="laomao", display_name="老猫",
                 password_hash=hash_password("pw"), is_platform_admin=True)
        s.add(u)
        s.commit()
        laomao_id = u.id
    c = app.test_client()
    assert c.post("/api/v1/auth/login",
                  json={"username": "laomao", "password": "pw"}).status_code == 200
    return {"client": c, "id": laomao_id}


def _mk_user(app, username: str, password: str = "pw", display: str = "") -> int:
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        u = User(username=username, display_name=display or username,
                 password_hash=hash_password(password))
        s.add(u)
        s.commit()
        return u.id


def _login(app, username: str, password: str = "pw"):
    c = app.test_client()
    assert c.post("/api/v1/auth/login",
                  json={"username": username, "password": password}).status_code == 200
    return c


def _deploy(client) -> int:
    preview = client.post("/api/v1/deployments/preview",
                          json={"description": "进销存"}).get_json()
    result = client.post("/api/v1/deployments/confirm", json={
        "spec_json": preview["spec_json"],
        "biz_name": "进销存",
        "seed_data": False,
    }).get_json()
    return result["deployment_id"]


def _api(did: int, name: str) -> str:
    return f"/api/v1/app/{did}/api/tables/{name}"


def _members(app, did: int) -> dict:
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        menus = (
            s.query(DeploymentMenu).filter_by(deployment_id=did)
            .order_by(DeploymentMenu.id).all()
        )
    return {"customers_menu": menus[0].id, "orders_menu": menus[1].id,
            "customers_label": menus[0].label, "orders_label": menus[1].label}


def _all_menu_labels(menus: list[dict]) -> list[str]:
    """平铺菜单树取全部节点 label。"""
    out: list[str] = []
    for m in menus:
        out.append(m["label"])
        out.extend(_all_menu_labels(m.get("children") or []))
    return out


def _all_menu_ids(menus: list[dict]) -> set[int]:
    ids: set[int] = set()
    for m in menus:
        ids.add(m["id"])
        ids |= _all_menu_ids(m.get("children") or [])
    return ids


def _grant(client, did: int, username: str, menu_ids: list[int], role: str = "operator"):
    resp = client.post(f"/api/v1/deployments/{did}/members",
                       json={"username": username, "role": role, "menu_ids": menu_ids})
    assert resp.status_code == 201, resp.get_data(as_text=True)


# ---------------- 创建人自动入册 / 非成员拦截 ----------------

def test_creator_is_auto_member(app, env, patch_llm):
    c = env["client"]
    did = _deploy(c)
    body = c.get(f"/api/v1/deployments/{did}/members").get_json()
    roles = {m["username"]: m["role"] for m in body["members"]}
    assert roles == {"laomao": "creator"}
    creator = body["members"][0]
    assert creator["menu_ids"] == [m["id"] for m in body["menus"]]
    assert creator["is_self"] is True


def test_non_member_blocked_everywhere(app, env, patch_llm):
    _mk_user(app, "outsider")
    c = _login(app, "outsider")
    did = _deploy(env["client"])
    assert c.get(f"/api/v1/app/{did}").status_code == 403
    assert c.get(f"/api/v1/app/{did}/api/meta").status_code == 403
    assert c.get(f"{_api(did, 'customers')}/rows").status_code == 403
    assert c.get(f"/api/v1/deployments/{did}/members").status_code == 403


# ---------------- 操作员：被授权菜单 + 我的记录 ----------------

def test_operator_sees_only_granted_menu_and_owns_records(app, env, patch_llm):
    c = env["client"]
    did = _deploy(c)
    m = _members(app, did)
    _mk_user(app, "xiaoli", display="小李")
    _grant(c, did, "xiaoli", [m["customers_menu"]])

    op = _login(app, "xiaoli")

    # 菜单/页面仅被授权的一个
    info = op.get(f"/api/v1/app/{did}").get_json()
    assert [x["label"] for x in info["menus"]] == [m["customers_label"]]
    assert info["role"] == "operator"
    page_tables = {p["table"] for p in info["pages"]}
    assert page_tables == {"customers"}

    # meta 仅被授权表
    meta = op.get(f"/api/v1/app/{did}/api/meta").get_json()
    assert set(meta) == {"customers"}

    # 操作未授权表被拒（registry 白名单外 → 404）
    assert op.get(f"{_api(did, 'orders')}/rows").status_code == 404

    # 数据：操作员自己的 vs 创建人的
    mine = op.post(f"{_api(did, 'customers')}/rows", json={"name": "小李客户"}).get_json()
    other = c.post(f"{_api(did, 'customers')}/rows", json={"name": "老猫客户"}).get_json()

    rows = op.get(f"{_api(did, 'customers')}/rows?page_size=50").get_json()
    assert rows["total"] == 1
    assert rows["data"][0]["id"] == mine["id"]
    assert rows["data"][0]["created_by"] == op.get("/api/v1/auth/me").get_json()["id"]

    # 操作员读/改/删他人的记录 → 404（不可见）
    assert op.get(f"{_api(did, 'customers')}/rows/{other['id']}").status_code == 404
    assert op.put(f"{_api(did, 'customers')}/rows/{other['id']}",
                  json={"phone": "1"}).status_code == 404
    assert op.delete(f"{_api(did, 'customers')}/rows/{other['id']}").status_code == 404

    # 操作员可改/删自己的记录
    assert op.put(f"{_api(did, 'customers')}/rows/{mine['id']}",
                  json={"phone": "2"}).status_code == 200
    assert op.delete(f"{_api(did, 'customers')}/rows/{mine['id']}").status_code == 200

    # 创建人仍能看到全部
    all_rows = c.get(f"{_api(did, 'customers')}/rows?page_size=50").get_json()
    assert all_rows["total"] == 1
    assert all_rows["data"][0]["name"] == "老猫客户"


def test_two_operators_different_menus(app, env, patch_llm):
    """同角色两人菜单不同（DR-02 核心）。"""
    c = env["client"]
    did = _deploy(c)
    m = _members(app, did)
    _mk_user(app, "xiaoli", display="小李")
    _mk_user(app, "xiaozhang", display="小张")
    _grant(c, did, "xiaoli", [m["customers_menu"]])
    _grant(c, did, "xiaozhang", [m["orders_menu"]])

    li = _login(app, "xiaoli")
    zhang = _login(app, "xiaozhang")
    li_info = li.get(f"/api/v1/app/{did}").get_json()
    zhang_info = zhang.get(f"/api/v1/app/{did}").get_json()
    assert [x["label"] for x in li_info["menus"]] == [m["customers_label"]]
    assert [x["label"] for x in zhang_info["menus"]] == [m["orders_label"]]
    assert {p["table"] for p in li_info["pages"]} == {"customers"}
    assert {p["table"] for p in zhang_info["pages"]} == {"orders"}


def test_operator_keeps_group_parent_of_granted_child(app, env, patch_llm):
    """授权某子菜单时，其祖先分组仍保留（可见菜单集合按整条路径呈现）。"""
    c = env["client"]
    did = _deploy(c)
    m = _members(app, did)
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        pages = s.query(PageSpec).filter_by(deployment_id=did).all()
        customers_page = next(
            p for p in pages if p.kind == "crud_list" and p.table_id
            and s.query(DeploymentMenu).filter_by(ref_id=p.id).first().label == m["customers_label"]
        )
        parent = DeploymentMenu(deployment_id=did, label="业务", kind="group", ref_id=None)
        s.add(parent)
        s.flush()
        child = DeploymentMenu(deployment_id=did, parent_id=parent.id,
                               label=m["customers_label"], kind="table", ref_id=customers_page.id)
        s.add(child)
        s.commit()
        parent_id, child_id = parent.id, child.id

    _mk_user(app, "xiaoli", display="小李")
    _grant(c, did, "xiaoli", [child_id])

    op = _login(app, "xiaoli")
    info = op.get(f"/api/v1/app/{did}").get_json()
    groups = [x for x in info["menus"] if x["ref_id"] is None]
    assert len(groups) == 1 and groups[0]["label"] == "业务"
    ids = _all_menu_ids(info["menus"])
    assert m["customers_label"] in _all_menu_labels(info["menus"])
    assert child_id in ids


# ---------------- 管理员：全套业务功能，但无工程室/成员变更 ----------------

def test_admin_full_menus_data_but_no_creator_powers(app, env, patch_llm):
    c = env["client"]
    did = _deploy(c)
    m = _members(app, did)
    _mk_user(app, "wangjie", display="王姐")
    _grant(c, did, "wangjie", [], role="admin")

    adm = _login(app, "wangjie")

    # 全套菜单 + 全量数据
    info = adm.get(f"/api/v1/app/{did}").get_json()
    assert info["role"] == "admin"
    assert {x["label"] for x in info["menus"]} == {m["customers_label"], m["orders_label"]}
    c.post(f"{_api(did, 'customers')}/rows", json={"name": "老猫客户"})
    adm_meta = adm.get(f"/api/v1/app/{did}/api/meta").get_json()
    assert set(adm_meta) == {"customers", "orders"}
    rows = adm.get(f"{_api(did, 'customers')}/rows").get_json()
    assert rows["total"] == 1

    # 可查看成员，但不可增删改（DR-14）
    assert adm.get(f"/api/v1/deployments/{did}/members").status_code == 200
    assert adm.post(f"/api/v1/deployments/{did}/members",
                    json={"username": "someone", "role": "operator"}).status_code == 403
    assert adm.put(f"/api/v1/deployments/{did}/members/{env['id']}",
                   json={"role": "admin"}).status_code == 403
    assert adm.delete(f"/api/v1/deployments/{did}/members/{env['id']}").status_code == 403

    # 无工程室（DR-14：管理员不含工程室）
    assert adm.get(f"/api/v1/deployments/{did}/blueprint").status_code == 403
    assert adm.post(f"/api/v1/deployments/{did}/tables/customers/columns",
                    json={"ops": [{"action": "change_type", "name": "phone", "type": "VARCHAR", "length": 64}]}
                    ).status_code == 403


# ---------------- 创建人独有：增删改成员 / 授权变更 ----------------

def test_operator_cannot_manage_members(app, env, patch_llm):
    c = env["client"]
    did = _deploy(c)
    m = _members(app, did)
    _mk_user(app, "xiaoli", display="小李")
    _mk_user(app, "xiaozhang", display="小张")
    _grant(c, did, "xiaoli", [m["customers_menu"]])

    op = _login(app, "xiaoli")
    assert op.get(f"/api/v1/deployments/{did}/members").status_code == 403
    assert op.post(f"/api/v1/deployments/{did}/members",
                   json={"username": "xiaozhang", "role": "operator",
                         "menu_ids": [m["orders_menu"]]}).status_code == 403
    ids = {u.id: u.username for u in
           app.extensions["datamason_session_factory"]().query(User).all() if u.username in ("xiaoli", "xiaozhang")}
    xiaozhang_id = next(v for v, k in ids.items() if k == "xiaozhang")
    assert op.put(f"/api/v1/deployments/{did}/members/{xiaozhang_id}",
                  json={"role": "operator", "menu_ids": [m["orders_menu"]]}).status_code == 403
    assert op.delete(f"/api/v1/deployments/{did}/members/{xiaozhang_id}").status_code == 403


def test_add_operator_requires_menus(app, env, patch_llm):
    c = env["client"]
    did = _deploy(c)
    _mk_user(app, "xiaoli")
    resp = c.post(f"/api/v1/deployments/{did}/members",
                  json={"username": "xiaoli", "role": "operator"})
    assert resp.status_code == 400
    assert "至少" in resp.get_json()["error"]

    resp = c.post(f"/api/v1/deployments/{did}/members",
                  json={"username": "xiaoli", "role": "operator", "menu_ids": [999]})
    assert resp.status_code == 400
    assert "不属于该部署" in resp.get_json()["error"]


def test_role_change_admin_operator_and_grants_update(app, env, patch_llm):
    c = env["client"]
    did = _deploy(c)
    m = _members(app, did)
    xiaoli_id = _mk_user(app, "xiaoli")
    _grant(c, did, "xiaoli", [], role="admin")

    # admin → operator 无菜单 → 400
    resp = c.put(f"/api/v1/deployments/{did}/members/{xiaoli_id}",
                 json={"role": "operator"})
    assert resp.status_code == 400

    # admin → operator 带菜单 → 生效，操作员只见分配菜单
    assert c.put(f"/api/v1/deployments/{did}/members/{xiaoli_id}",
                 json={"role": "operator", "menu_ids": [m["customers_menu"]]}).status_code == 200
    op = _login(app, "xiaoli")
    info = op.get(f"/api/v1/app/{did}").get_json()
    assert {x["label"] for x in info["menus"]} == {m["customers_label"]}

    # 改授权：换到 orders 菜单后 customers 不再可见
    assert c.put(f"/api/v1/deployments/{did}/members/{xiaoli_id}",
                 json={"menu_ids": [m["orders_menu"]]}).status_code == 200
    info = op.get(f"/api/v1/app/{did}").get_json()
    assert {x["label"] for x in info["menus"]} == {m["orders_label"]}


def test_remove_member_clears_grants_and_access(app, env, patch_llm):
    c = env["client"]
    did = _deploy(c)
    m = _members(app, did)
    xiaoli_id = _mk_user(app, "xiaoli")
    _grant(c, did, "xiaoli", [m["customers_menu"]])
    op = _login(app, "xiaoli")
    assert op.get(f"/api/v1/app/{did}").status_code == 200

    assert c.delete(f"/api/v1/deployments/{did}/members/{xiaoli_id}").status_code == 200
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        assert s.query(DeploymentMember).filter_by(user_id=xiaoli_id, deployment_id=did).count() == 0
    assert op.get(f"/api/v1/app/{did}").status_code == 403

    # 创建人不可被移除，也不可自移
    assert c.delete(f"/api/v1/deployments/{did}/members/{env['id']}").status_code == 400
    assert c.delete(f"/api/v1/deployments/{did}/members/{env['id']}").status_code == 400


# ---------------- 归属列防改（DR-05 强化） ----------------

def test_ownership_columns_cannot_be_overridden(app, env, patch_llm):
    c = env["client"]
    did = _deploy(c)
    resp = c.post(f"{_api(did, 'customers')}/rows", json={
        "name": "防改", "created_by": 999, "updated_by": 999,
    })
    assert resp.status_code == 201
    cid = resp.get_json()["id"]
    one = c.get(f"{_api(did, 'customers')}/rows/{cid}").get_json()
    assert one["created_by"] == env["id"]
    assert one["updated_by"] == env["id"]
    assert one["name"] == "防改"


# ---------------- 控制台数据接口同受成员校验 ----------------

def test_console_data_routes_member_checked(app, env, patch_llm):
    c = env["client"]
    did = _deploy(c)
    _mk_user(app, "xiaoli")
    _grant(c, did, "xiaoli", [_members(app, did)["customers_menu"]])
    op = _login(app, "xiaoli")
    # 部署清单只含自己参与的（容器里仅一个部署，创建人自己也可见）
    assert op.get("/api/v1/deployments").status_code == 200
    # 控制台数据浏览：成员可看被授权表，未授权表 404
    assert op.get(f"/api/v1/deployments/{did}/tables").status_code == 200
    assert op.get(f"/api/v1/deployments/{did}/tables/customers/rows").status_code == 200
    assert op.get(f"/api/v1/deployments/{did}/tables/orders/rows").status_code == 404