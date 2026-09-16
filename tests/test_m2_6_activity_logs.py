"""M2-6 验收：操作日志（DR-03 两阶段写入 + 启动对账）。

- 业务写路径（create/update/delete）产出 committed 日志；UPDATE 只存变更字段、
  DELETE 记录可读摘要；日志只追加、无 update/delete 路由
- 系统动作日志：deploy / member（增删改）/ structure（字段变更）
- 查询：rest 倒序分页 + 四维筛选（时间/人/动作/业务对象）；pending 不进列表
- 权限：creator/admin 全量；operator 强制只见自己；非成员 403
- 启动对账：崩溃在 ②③ 之间 → pending 按业务实际生效性收尾/丢弃
"""
from __future__ import annotations

from datetime import datetime

import pytest

from server.audit.service import AuditSession, reconcile_pending
from server.models import ActivityLog

GUESTBOOK_SPEC = {
    "biz_notes": "访客留言",
    "tables": [
        {
            "name": "messages",
            "biz_name": "留言",
            "columns": [
                {"name": "id", "biz_name": "主键", "type": "INTEGER",
                 "is_pk": True, "autoincrement": True, "nullable": False},
                {"name": "visitor", "biz_name": "访客", "type": "VARCHAR",
                 "length": 64, "nullable": False},
                {"name": "content", "biz_name": "内容", "type": "TEXT"},
            ],
            "indexes": [],
        }
    ],
}

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
    ],
}

ACTION_LABEL = {
    "create": "新建", "update": "更新", "delete": "删除",
    "deploy": "部署", "member": "成员", "structure": "结构变更",
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
def admin(app, client):
    from server.auth.service import hash_password
    from server.models import User

    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        u = User(username="laomao", display_name="老猫",
                 password_hash=hash_password("pw"), is_platform_admin=True)
        s.add(u)
        s.commit()
    assert client.post("/api/v1/auth/login",
                       json={"username": "laomao", "password": "pw"}).status_code == 200
    return u.id


@pytest.fixture(autouse=True)
def patch_llm(monkeypatch):
    monkeypatch.setattr(
        "server.settings.service.get_default_llm_client",
        lambda _app: FakeLlm(LINKED_SPEC),
    )
    yield


def _deploy(client, spec_key: str = "linked") -> int:
    preview = client.post("/api/v1/deployments/preview",
                          json={"description": "测试"}).get_json()
    result = client.post("/api/v1/deployments/confirm", json={
        "spec_json": preview["spec_json"],
        "biz_name": "进销存验证",
        "seed_data": False,
    }).get_json()
    return result["deployment_id"]


def _api(did: int, name: str):
    return f"/api/v1/app/{did}/api/tables/{name}"


def _logs(client, did: int, **params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"/api/v1/app/{did}/api/activity-logs" + (f"?{query}" if query else "")
    return client.get(url).get_json()


def _by_action(logs: list[dict], action: str) -> list[dict]:
    return [lg for lg in logs if lg["action"] == action]


# ---------------- 业务写路径：committed 日志 ----------------

def test_crud_writes_produce_committed_logs(app, client, admin):
    did = _deploy(client)
    base = _api(did, "customers")

    assert client.post(f"{base}/rows",
                       json={"name": "老李", "phone": "13900000001"}).status_code == 201
    logs = _logs(client, did)["logs"]
    creates = _by_action(logs, "create")
    assert len(creates) == 1
    create = creates[0]
    assert create["object_table"] == "customers"
    assert create["object_biz_name"] == "客户"
    assert create["object_pk"] == "1"
    assert create["object_label"] == "老李"
    assert create["actor_display"] == "老猫"
    assert create["changes"]["name"] == {"old": None, "new": "老李"}
    assert create["changes"]["phone"] == {"old": None, "new": "13900000001"}
    assert not any(k in create["changes"] for k in
                   ("created_by", "created_at", "updated_by", "updated_at"))


def test_update_log_only_changed_fields(app, client, admin):
    did = _deploy(client)
    base = _api(did, "customers")
    cid = client.post(f"{base}/rows",
                      json={"name": "小李", "phone": "13900000002"}).get_json()["id"]

    assert client.put(f"{base}/rows/{cid}",
                      json={"phone": "13800000002"}).status_code == 200
    updates = _by_action(_logs(client, did)["logs"], "update")
    assert len(updates) == 1
    up = updates[0]
    assert up["object_pk"] == str(cid)
    assert up["object_label"] == "小李"
    assert up["changes"] == {"phone": {"old": "13900000002", "new": "13800000002"}}
    assert "name" not in up["changes"]


def test_delete_log_label_remains_readable(app, client, admin):
    did = _deploy(client)
    base = _api(did, "customers")
    cid = client.post(f"{base}/rows",
                      json={"name": "老王", "phone": "1"}).get_json()["id"]

    assert client.delete(f"{base}/rows/{cid}").get_json()["deleted"] is True
    assert client.get(f"{base}/rows/{cid}").status_code == 404
    deletes = _by_action(_logs(client, did)["logs"], "delete")
    assert len(deletes) == 1
    de = deletes[0]
    assert de["object_pk"] == str(cid)
    assert de["object_label"] == "老王"


def test_logs_desc_order_and_append_only(app, client, admin):
    did = _deploy(client)
    base = _api(did, "customers")
    for i in range(3):
        client.post(f"{base}/rows", json={"name": f"客户{i}", "phone": str(i)})

    ids = [lg["id"] for lg in _logs(client, did)["logs"]]
    assert ids == sorted(ids, reverse=True)

    # 只追加：无 update/delete 日志路由
    assert client.put(f"/api/v1/app/{did}/api/activity-logs/1",
                      json={"object_label": "改"}).status_code == 405
    assert client.delete(f"/api/v1/app/{did}/api/activity-logs/1").status_code == 405


# ---------------- 权限裁剪 ----------------

def _make_second_user(app, client) -> int:
    from server.auth.service import hash_password
    from server.models import User

    assert client.post("/api/v1/auth/users", json={
        "username": "xiaoli", "display_name": "小李", "password": "li123",
    }).status_code == 201
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        return s.query(User).filter_by(username="xiaoli").one().id


def test_operator_sees_only_own_logs(app, client, admin):
    did = _deploy(client)
    li_id = _make_second_user(app, client)
    menus = client.get(f"/api/v1/deployments/{did}/members").get_json()["menus"]

    # 管理员写入一条
    assert client.post(f"{_api(did, 'customers')}/rows",
                       json={"name": "老猫客户"}).status_code == 201

    # 小李先进为 admin（触发 member 日志），再改格为 operator 并授权全部菜单
    assert client.post(f"/api/v1/deployments/{did}/members", json={
        "username": "xiaoli", "role": "admin",
    }).status_code == 201
    assert client.put(f"/api/v1/deployments/{did}/members/{li_id}", json={
        "role": "operator", "menu_ids": [m["id"] for m in menus],
    }).status_code == 200

    # 小李写一条自己的
    assert client.post("/api/v1/auth/logout").status_code == 200
    assert client.post("/api/v1/auth/login",
                       json={"username": "xiaoli", "password": "li123"}).status_code == 200
    assert client.post(f"{_api(did, 'customers')}/rows",
                       json={"name": "小李客户"}).status_code == 201

    own = _logs(client, did)
    assert own["total"] >= 1
    assert {lg["actor_user_id"] for lg in own["logs"]} == {li_id}
    assert any(lg["object_label"] == "小李客户" for lg in own["logs"])

    # 管理员看全量（含小李的）
    assert client.post("/api/v1/auth/logout").status_code == 200
    assert client.post("/api/v1/auth/login",
                       json={"username": "laomao", "password": "pw"}).status_code == 200
    all_logs = _logs(client, did)["logs"]
    assert any(lg["actor_user_id"] == li_id and lg["object_label"] == "小李客户"
               for lg in all_logs)


def test_activity_logs_requires_member(app, client, admin):
    did = _deploy(client)
    _make_second_user(app, client)
    assert client.post("/api/v1/auth/logout").status_code == 200
    assert client.post("/api/v1/auth/login",
                       json={"username": "xiaoli", "password": "li123"}).status_code == 200
    assert client.get(f"/api/v1/app/{did}/api/activity-logs").status_code == 403


# ---------------- 四维筛选 ----------------

def test_four_dimension_filters(app, client, admin):
    did = _deploy(client)
    base = _api(did, "customers")
    cid = client.post(f"{base}/rows",
                      json={"name": "筛选目标", "phone": "1"}).get_json()["id"]
    client.put(f"{base}/rows/{cid}", json={"phone": "2"})
    client.delete(f"{base}/rows/{cid}")

    occ = _by_action(_logs(client, did)["logs"], "create")[0]["occurred_at"]

    # 全量含 deploy 系统日志：create/update/delete 3 条 + deploy 1 条
    total_all = _logs(client, did)["total"]
    assert total_all == 4

    by_actor = _logs(client, did, actor_user_id=admin)
    assert by_actor["total"] == total_all

    by_action = _logs(client, did, action="delete")["logs"]
    assert len(by_action) == 1 and by_action[0]["action"] == "delete"

    by_table = _logs(client, did, object_table="customers")
    assert by_table["total"] == 3
    assert _logs(client, did, object_table="ghost")["total"] == 0

    by_pk = _logs(client, did, object_table="customers", object_pk=str(cid))
    assert by_pk["total"] == 3

    # 时间区间：from 含（>=），to 不含（<）
    got = _logs(client, did, from_=occ)
    assert any(lg["occurred_at"] == occ for lg in got["logs"])
    got_to = _logs(client, did, to=occ)
    assert not any(lg["occurred_at"] == occ for lg in got_to["logs"])


def test_pending_not_listed(app, client, admin):
    did = _deploy(client)
    AuditSession(app, did, admin).begin("create", "customers",
                                        changes={"name": {"old": None, "new": "影子"}})
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        pend = [r.id for r in s.query(ActivityLog)
                .filter_by(deployment_id=did, status="pending").all()]
    assert pend, "pending 应存在"
    logs = _logs(client, did)
    assert not any(lg["id"] in pend for lg in logs["logs"])
    assert not any(lg["object_label"] == "影子" for lg in logs["logs"])


# ---------------- 系统动作日志 ----------------

def test_deploy_log_on_confirm(app, client, admin):
    did = _deploy(client)
    deploys = _by_action(_logs(client, did)["logs"], "deploy")
    assert len(deploys) == 1
    de = deploys[0]
    assert de["object_kind"] == "deployment"
    assert de["actor_user_id"] == admin
    assert "进销存验证" in de["object_label"]
    assert de["changes"]["table_count"] == 1


def test_member_and_structure_logs(app, client, admin):
    did = _deploy(client)
    li_id = _make_second_user(app, client)
    menus = client.get(f"/api/v1/deployments/{did}/members").get_json()["menus"]

    assert client.post(f"/api/v1/deployments/{did}/members", json={
        "username": "xiaoli", "role": "operator", "menu_ids": [m["id"] for m in menus],
    }).status_code == 201
    assert client.put(f"/api/v1/deployments/{did}/members/{li_id}", json={
        "role": "admin",
    }).status_code == 200
    assert client.delete(f"/api/v1/deployments/{did}/members/{li_id}").status_code == 200
    assert client.post(f"/api/v1/deployments/{did}/tables/customers/columns",
                       json={"ops": [{"action": "add", "name": "email",
                                      "biz_name": "邮箱", "type": "VARCHAR",
                                      "length": 128}]}).status_code == 200

    member_logs = _by_action(_logs(client, did)["logs"], "member")
    by_type = {lg["changes"]["type"]: lg for lg in member_logs}
    # 同秒事件按 id 倒序 = 最晚发生在前
    assert [lg["changes"]["type"] for lg in member_logs] == ["remove", "update", "add"]
    assert by_type["add"]["object_label"] == "xiaoli 加入（操作员）"
    assert by_type["update"]["changes"] == {"type": "update",
                                            "old_role": "operator", "new_role": "admin"}
    assert by_type["remove"]["changes"] == {"type": "remove"}

    structure = _by_action(_logs(client, did)["logs"], "structure")
    assert len(structure) == 1
    assert structure[0]["changes"]["ops"] == [{"action": "add", "name": "email"}]
    assert structure[0]["object_table"] == "customers"


# ---------------- 启动对账（DR-03 crash 收尾） ----------------

def _begin(app, did, uid, action, table, **kw):
    return AuditSession(app, did, uid).begin(action, table, **kw)


def test_reconcile_create_crash_after_business_commit(app, client, admin):
    did = _deploy(client)
    cid = client.post(f"{_api(did, 'customers')}/rows",
                      json={"name": "崩溃恢复", "phone": "7"}).get_json()["id"]

    # 模拟 ② 已提交但 ③ 未写（进程崩溃）：pending 留库 → 对账按值反查生效 → committed
    lid = _begin(app, did, admin, "create", "customers",
                 object_pk=str(cid),
                 changes={"name": {"old": None, "new": "崩溃恢复"},
                          "phone": {"old": None, "new": "7"}})
    assert reconcile_pending(app) == 1
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        assert s.get(ActivityLog, lid).status == "committed"


def test_reconcile_create_crash_business_not_committed(app, client, admin):
    did = _deploy(client)
    # ② 未发生：只有 pending，无业务行 → 对账丢弃
    lid = _begin(app, did, admin, "create", "customers",
                 changes={"name": {"old": None, "new": "没有发生"}})
    assert reconcile_pending(app) == 1
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        assert s.get(ActivityLog, lid) is None
    assert not _by_action(_logs(client, did)["logs"], "create")


def test_reconcile_delete_effective_and_ineffective(app, client, admin):
    did = _deploy(client)
    base = _api(did, "customers")
    cid = client.post(f"{base}/rows",
                      json={"name": "删单测试", "phone": "8"}).get_json()["id"]
    cid2 = client.post(f"{base}/rows",
                       json={"name": "删单测试2", "phone": "80"}).get_json()["id"]

    # ① ② 完成（行已删）但 ③ 未写 → 主键不存在 → committed
    lid1 = _begin(app, did, admin, "delete", "customers", object_pk=str(cid))
    assert client.delete(f"{base}/rows/{cid}").get_json()["deleted"] is True
    assert reconcile_pending(app) == 1
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        assert s.get(ActivityLog, lid1).status == "committed"

    # ② 未发生（行还在）→ pending 丢弃
    lid2 = _begin(app, did, admin, "delete", "customers", object_pk=str(cid2))
    assert reconcile_pending(app) == 1
    with Session() as s:
        assert s.get(ActivityLog, lid2) is None


def test_reconcile_update_matches_or_discards(app, client, admin):
    did = _deploy(client)
    base = _api(did, "customers")
    cid = client.post(f"{base}/rows",
                      json={"name": "改单测试", "phone": "9"}).get_json()["id"]

    # 业务已更新到位 → 对账按目标值命中 → committed
    lid1 = _begin(app, did, admin, "update", "customers", object_pk=str(cid),
                  changes={"phone": {"old": "9", "new": "10"}})
    assert client.put(f"{base}/rows/{cid}", json={"phone": "10"}).status_code == 200
    assert reconcile_pending(app) == 1
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        assert s.get(ActivityLog, lid1).status == "committed"

    # 目标值落空（业务没改到） → 丢弃
    lid2 = _begin(app, did, admin, "update", "customers", object_pk=str(cid),
                  changes={"phone": {"old": "10", "new": "999"}})
    assert reconcile_pending(app) == 1
    with Session() as s:
        assert s.get(ActivityLog, lid2) is None