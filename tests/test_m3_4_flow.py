"""M3-4 验收：流程向导 v0（DR-06，向导式流程，非工作流引擎）。

- 编排（创建人独有 DR-14）：GET/POST/PUT/DELETE /deployments/{id}/flows；
  新建流程同步生成 kind=flow 页面（携带 flow_id，供菜单 kind=flow 挂载）
- 校验：步骤名/页面归属/状态字段在表内/值随字段/不指向流程页/条数上限；管理员 403
- 运行态（GET /app/{id}/flows/{fid}）：游标推导 已完成/当前/待做；当前步完成条件 met
- 推进（POST .../advance）：完成条件不满足 409；满足后把边界记录推进为状态值，
  写 activity_logs（action=flow），游标+1 → 状态字段随步推进（待收货→已收货→已入库）；
  全部完成后再推进 409
- 操作员可见性（DR-02）：流程菜单授权后才可见/可推进
- 删除流程：连带清理挂载菜单，避免工作台悬空引用
"""
from __future__ import annotations

import pytest

from datamason.auth.service import hash_password
from datamason.models import ActivityLog, DeploymentMember, DeploymentMenu, FlowSpec, PageSpec, User

FLOW_SPEC = {
    "biz_notes": "入库流程：收货登记 → 质检 → 入库确认",
    "tables": [
        {
            "name": "goods",
            "biz_name": "入库单",
            "columns": [
                {"name": "id", "biz_name": "主键", "type": "INTEGER",
                 "is_pk": True, "autoincrement": True, "nullable": False},
                {"name": "name", "biz_name": "货物", "type": "VARCHAR",
                 "length": 64, "nullable": False},
                {"name": "status", "biz_name": "进度", "type": "VARCHAR", "length": 16},
            ],
            "indexes": [],
        }
    ],
}


class FakeLlm:
    def chat_json(self, system_prompt, user_prompt):
        class _Usage:
            tokens_in = 1
            tokens_out = 1
            duration_ms = 1

        return FLOW_SPEC, _Usage()


@pytest.fixture()
def patch_llm(monkeypatch):
    monkeypatch.setattr(
        "datamason.settings.service.get_default_llm_client",
        lambda _app: FakeLlm(),
    )
    yield


@pytest.fixture()
def env(app):
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


def _mk_user(app, username: str) -> int:
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        u = User(username=username, display_name=username,
                 password_hash=hash_password("pw"))
        s.add(u)
        s.commit()
        return u.id


def _login(app, username: str, password: str = "pw"):
    c = app.test_client()
    assert c.post("/api/v1/auth/login",
                  json={"username": username, "password": password}).status_code == 200
    return c


def _session(app):
    return app.extensions["datamason_session_factory"]()


def _deploy(client) -> int:
    preview = client.post("/api/v1/deployments/preview",
                          json={"description": "入库流程"}).get_json()
    result = client.post("/api/v1/deployments/confirm", json={
        "spec_json": preview["spec_json"],
        "biz_name": "入库演示",
        "seed_data": False,
    }).get_json()
    return result["deployment_id"]


def _crud_page(env, did: int) -> dict:
    payload = env["client"].get(f"/api/v1/deployments/{did}/interfaces").get_json()
    return next(p for p in payload["pages"] if p["kind"] == "crud_list")


def _steps(crud: dict) -> list[dict]:
    return [
        {"name": "收货登记", "page_id": crud["id"],
         "done_when": {"type": "records", "min": 1},
         "status_field": "status", "status_value": "待收货"},
        {"name": "质检", "page_id": crud["id"],
         "done_when": {"type": "records", "min": 1},
         "status_field": "status", "status_value": "已收货"},
        {"name": "入库确认", "page_id": crud["id"],
         "done_when": {"type": "records", "min": 1},
         "status_field": "status", "status_value": "已入库"},
    ]


def _make_flow(env, did: int) -> dict:
    crud = _crud_page(env, did)
    resp = env["client"].post(f"/api/v1/deployments/{did}/flows",
                              json={"name": "入库流程", "steps": _steps(crud)})
    assert resp.status_code == 201, resp.get_data(as_text=True)
    body = resp.get_json()
    assert len(body["steps"]) == 3 and body["steps"][0]["name"] == "收货登记"
    return body


def _flow_logs(app, did: int) -> list[dict]:
    with _session(app) as s:
        rows = (
            s.query(ActivityLog)
            .filter(ActivityLog.deployment_id == did,
                    ActivityLog.action == "flow",
                    ActivityLog.status == "committed")
            .order_by(ActivityLog.id)
            .all()
        )
        return [{"object_label": r.object_label, "changes": r.changes} for r in rows]


def test_flow_create_creates_flow_page_for_menu(app, env, patch_llm):
    did = _deploy(env["client"])
    flow = _make_flow(env, did)

    with _session(app) as s:
        f = s.get(FlowSpec, flow["id"])
        assert f is not None and f.name == "入库流程" and f.version == 1
        assert f.steps[0]["page_id"] == flow["steps"][0]["page_id"]
        fp = s.query(PageSpec).filter_by(deployment_id=did, kind="flow").first()
        assert fp is not None and fp.flow_id == flow["id"] and fp.title == "入库流程"

    # 流程页进入工作台/界面卡（带 flow_id，供向导渲染）
    ws = env["client"].get(f"/api/v1/app/{did}").get_json()
    flow_page = next(p for p in ws["pages"] if p["kind"] == "flow")
    assert flow_page["flow_id"] == flow["id"]
    itf = env["client"].get(f"/api/v1/deployments/{did}/interfaces").get_json()
    assert next(p for p in itf["pages"] if p["id"] == flow_page["id"])["flow_id"] == flow["id"]
    # 流程清单
    flows = env["client"].get(f"/api/v1/deployments/{did}/flows").get_json()["flows"]
    assert flows[0]["page_id"] == flow_page["id"]


def test_flow_save_validation(app, env, patch_llm):
    did = _deploy(env["client"])
    crud = _crud_page(env, did)
    steps = _steps(crud)

    cases = [
        ({"name": "", "steps": steps}, "流程名称"),
        ({"name": "入库流程", "steps": []}, "步骤数量"),
        ({"name": "入库流程", "steps": [{"name": "x", "page_id": 999999}]}, "页面不属于该部署"),
        ({"name": "入库流程", "steps": [{"name": "x", "page_id": crud["id"],
                                        "status_field": "no_such"}]}, "状态字段"),
        ({"name": "入库流程", "steps": [{"name": "x", "page_id": crud["id"],
                                        "status_value": "v"}]}, "推进值"),
        ({"name": "入库流程", "steps": [{"name": "x", "page_id": crud["id"],
                                        "done_when": {"type": "magic", "min": 1}}]}, "完成条件"),
        ({"name": "入库流程", "steps": [{"name": "x", "page_id": None}]}, "页面"),
    ]
    for payload, hint in cases:
        resp = env["client"].post(f"/api/v1/deployments/{did}/flows", json=payload)
        assert resp.status_code == 400, (hint, resp.get_data(as_text=True))
        assert hint in resp.get_json()["error"]

    # 指向流程页 → 400（避免向导递归）
    with _session(app) as s:
        flow_page_id = s.query(PageSpec).filter_by(deployment_id=did, kind="flow").first()
        if flow_page_id is not None:
            bad = env["client"].post(f"/api/v1/deployments/{did}/flows", json={
                "name": "循环", "steps": [{"name": "x", "page_id": flow_page_id.id}]})
            assert bad.status_code == 400

    # 管理员创建人 403（DR-14）
    adm_id = _mk_user(app, "adm")
    with _session(app) as s:
        s.add(DeploymentMember(deployment_id=did, user_id=adm_id, role="admin"))
        s.commit()
    adm = _login(app, "adm")
    assert adm.get(f"/api/v1/deployments/{did}/flows").status_code == 403
    assert adm.post(f"/api/v1/deployments/{did}/flows",
                    json={"name": "X", "steps": steps}).status_code == 403


def test_flow_advance_blocked_until_condition_met(app, env, patch_llm):
    did = _deploy(env["client"])
    flow = _make_flow(env, did)
    fid = flow["id"]

    # 初始：未录入数据 → 当前步完成条件不满足
    rt = env["client"].get(f"/api/v1/app/{did}/flows/{fid}").get_json()
    assert rt["done"] is False and rt["current_index"] == 0
    assert rt["steps"][0]["state"] == "current" and rt["steps"][0]["met"] is False
    assert rt["steps"][1]["state"] == "pending"
    # 条件不满足 → 推进被拒
    resp = env["client"].post(f"/api/v1/app/{did}/flows/{fid}/advance")
    assert resp.status_code == 409, resp.get_data(as_text=True)
    assert "完成条件不满足" in resp.get_json()["error"]


def test_flow_progresses_status_and_logs(app, env, patch_llm):
    did = _deploy(env["client"])
    flow = _make_flow(env, did)
    fid = flow["id"]

    # 步骤0：创建一条收货记录（status 为空）→ 条件满足 → 下一步
    created = env["client"].post(f"/api/v1/app/{did}/api/tables/goods/rows",
                                 json={"name": "水泥"}).get_json()
    pk = created["id"]
    assert env["client"].get(f"/api/v1/app/{did}/flows/{fid}").get_json()["steps"][0]["met"]

    adv = env["client"].post(f"/api/v1/app/{did}/flows/{fid}/advance")
    assert adv.status_code == 200, adv.get_data(as_text=True)
    assert adv.get_json()["current_index"] == 1 and adv.get_json()["done"] is False
    row = env["client"].get(f"/api/v1/app/{did}/api/tables/goods/rows/{pk}").get_json()
    assert row["status"] == "待收货"

    # 步骤1 质检 → 已收货
    adv = env["client"].post(f"/api/v1/app/{did}/flows/{fid}/advance")
    assert adv.status_code == 200, adv.get_data(as_text=True)
    row = env["client"].get(f"/api/v1/app/{did}/api/tables/goods/rows/{pk}").get_json()
    assert row["status"] == "已收货"

    # 步骤2 入库确认 → 已入库 → 流程完成
    adv = env["client"].post(f"/api/v1/app/{did}/flows/{fid}/advance")
    assert adv.status_code == 200 and adv.get_json()["done"] is True
    row = env["client"].get(f"/api/v1/app/{did}/api/tables/goods/rows/{pk}").get_json()
    assert row["status"] == "已入库"
    rt = env["client"].get(f"/api/v1/app/{did}/flows/{fid}").get_json()
    assert rt["done"] is True and all(st["state"] == "done" for st in rt["steps"])

    # 完成后再次推进 → 409
    resp = env["client"].post(f"/api/v1/app/{did}/flows/{fid}/advance")
    assert resp.status_code == 409

    # 每步完成写 activity_logs（action=flow，含第三步入库确认）
    step_logs = [lg for lg in _flow_logs(app, did) if "step_name" in lg["changes"]]
    assert [lg["changes"]["step_name"] for lg in step_logs] == ["收货登记", "质检", "入库确认"]
    assert step_logs[-1]["changes"]["status_value"] == "已入库"
    assert any("入库流程" in lg["object_label"] for lg in step_logs)


def test_flow_status_write_scoped_chain(app, env, patch_llm):
    """边界记录接链：新增的第二条记录从 NULL 一路推进到最终状态。"""
    did = _deploy(env["client"])
    flow = _make_flow(env, did)
    fid = flow["id"]
    env["client"].post(f"/api/v1/app/{did}/api/tables/goods/rows", json={"name": "A"})
    adv = env["client"].post(f"/api/v1/app/{did}/flows/{fid}/advance")
    assert adv.status_code == 200
    # 边界记录 = 空 或 上一步值：步骤1 推进同时带走「A(待收货)」与「B(空)」→ 均到下一步值
    pk2 = env["client"].post(f"/api/v1/app/{did}/api/tables/goods/rows",
                             json={"name": "B"}).get_json()["id"]
    env["client"].post(f"/api/v1/app/{did}/flows/{fid}/advance")
    env["client"].post(f"/api/v1/app/{did}/flows/{fid}/advance")
    rows = env["client"].get(
        f"/api/v1/app/{did}/api/tables/goods/rows?page_size=50").get_json()["data"]
    assert {r["status"] for r in rows if r["id"] != pk2} == {"已入库"}
    assert next(r for r in rows if r["id"] == pk2)["status"] == "已入库"


def test_flow_operator_visible_after_grant(app, env, patch_llm):
    did = _deploy(env["client"])
    flow = _make_flow(env, did)
    fid = flow["id"]
    itf = env["client"].get(f"/api/v1/deployments/{did}/interfaces").get_json()
    flow_page = next(p for p in itf["pages"] if p["kind"] == "flow")
    crud_page = next(p for p in itf["pages"] if p["kind"] == "crud_list")
    op_id = _mk_user(app, "op")

    # 创建人挂流程菜单（kind=flow）+ 授权 crud 页面菜单给操作员
    nodes = [
        {"label": "流程向导", "kind": "flow", "ref_id": flow_page["id"], "parent_id": None},
        {"label": "入库单", "kind": "table", "ref_id": crud_page["id"], "parent_id": -1},
    ]
    saved = env["client"].put(f"/api/v1/deployments/{did}/menus",
                              json={"nodes": nodes}).get_json()["nodes"]
    flow_menu = next(m for m in saved if m["kind"] == "flow")
    env["client"].post(f"/api/v1/deployments/{did}/members",
                       json={"username": "op", "role": "operator",
                             "menu_ids": [flow_menu["id"]]})
    op = _login(app, "op")
    assert op.get(f"/api/v1/app/{did}/flows/{fid}").status_code == 200

    # 未授权的另一个操作员 → 403（DR-02 功能级裁剪）
    op2_id = _mk_user(app, "op2")
    with _session(app) as s:
        from datamason.models import DeploymentMenu as _DM
        s.add(DeploymentMember(deployment_id=did, user_id=op2_id, role="operator"))
        s.add(_DM(deployment_id=did, label="其他", kind="table"))
        s.commit()
    op2 = _login(app, "op2")
    assert op2.get(f"/api/v1/app/{did}/flows/{fid}").status_code == 403

    # 操作员推进（自己的记录）
    op.post(f"/api/v1/app/{did}/api/tables/goods/rows", json={"name": "水泥"})
    adv = op.post(f"/api/v1/app/{did}/flows/{fid}/advance")
    assert adv.status_code == 200, adv.get_data(as_text=True)
    assert adv.get_json()["current_index"] == 1
    import json as _json

    with _session(app) as s:
        f = s.get(FlowSpec, fid)
        progress = _json.loads(f.progress_json or "{}")
        assert int(progress[str(op_id)]) == 1  # 游标按用户推进


def test_flow_update_and_delete_cleanup(app, env, patch_llm):
    did = _deploy(env["client"])
    flow = _make_flow(env, did)
    fid = flow["id"]
    crud = _crud_page(env, did)

    # 更新：改名 + 换步骤
    resp = env["client"].put(f"/api/v1/deployments/{did}/flows/{fid}",
                             json={"name": "质检入库双步",
                                   "steps": [{"name": "质检", "page_id": crud["id"]}]})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["name"] == "质检入库双步" and len(resp.get_json()["steps"]) == 1
    with _session(app) as s:
        assert s.get(FlowSpec, fid).version == 2

    # 挂菜单后删除 → 流程页与菜单一并清理（不悬空）
    itf = env["client"].get(f"/api/v1/deployments/{did}/interfaces").get_json()
    flow_page = next(p for p in itf["pages"] if p["kind"] == "flow")
    env["client"].put(f"/api/v1/deployments/{did}/menus",
                      json={"nodes": [{"label": "向导", "kind": "flow",
                                       "ref_id": flow_page["id"], "parent_id": None}]})
    resp = env["client"].delete(f"/api/v1/deployments/{did}/flows/{fid}")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    with _session(app) as s:
        assert s.get(FlowSpec, fid) is None
        assert s.query(PageSpec).filter_by(flow_id=fid).count() == 0
        assert s.query(DeploymentMenu).filter_by(ref_id=flow_page["id"]).count() == 0
    assert any("已删除" in lg["object_label"] for lg in _flow_logs(app, did))
    assert env["client"].delete(f"/api/v1/deployments/{did}/flows/{fid}").status_code == 404


def test_flow_advance_writes_two_phase_flow_log(app, env, patch_llm):
    """真实推进写 committed 的 flow 日志（object_kind=flow），含边界主键与推进条数。"""
    did = _deploy(env["client"])
    flow = _make_flow(env, did)
    base = f"/api/v1/app/{did}/api/tables/goods/rows"
    r = env["client"].post(base, json={"name": "货X"}).get_json()
    assert env["client"].post(
        f"/api/v1/app/{did}/flows/{flow['id']}/advance").status_code == 200
    with _session(app) as s:
        lg = (s.query(ActivityLog)
              .filter_by(deployment_id=did, action="flow")
              .order_by(ActivityLog.id.desc()).first())
        assert lg is not None and lg.status == "committed" and lg.object_kind == "flow"
        assert lg.object_table == "goods"
        assert str(r["id"]) in lg.changes["affected_pks"]
        assert lg.changes["records_advanced"] >= 1


def test_flow_advance_pending_reconcile(app, env, patch_llm):
    """DR-03 两阶段：流程推进崩溃后，启动对账按边界记录状态收尾/丢弃。"""
    from datamason.audit.service import AuditSession, reconcile_pending

    did = _deploy(env["client"])
    flow = _make_flow(env, did)
    base = f"/api/v1/app/{did}/api/tables/goods/rows"

    def pending(pk: int) -> int:
        return AuditSession(app, did, env["id"]).begin(
            "flow", "goods", object_kind="flow", object_pk=str(flow["id"]),
            object_label="流程「入库流程」·收货登记 完成",
            changes={"step_name": "收货登记", "step_index": 0,
                     "status_field": "status", "status_value": "待收货",
                     "affected_pks": [str(pk)]})

    # 情形一：业务已写入目标状态 → 对账收尾为 committed
    r1 = env["client"].post(base, json={"name": "货A"}).get_json()
    lid1 = pending(r1["id"])
    assert env["client"].put(f"{base}/{r1['id']}", json={"status": "待收货"}).status_code == 200
    assert reconcile_pending(app) == 1
    with _session(app) as s:
        assert s.get(ActivityLog, lid1).status == "committed"

    # 情形二：业务未写入（仍是空状态）→ 丢弃 pending
    r2 = env["client"].post(base, json={"name": "货B"}).get_json()
    lid2 = pending(r2["id"])
    assert reconcile_pending(app) == 1
    with _session(app) as s:
        assert s.get(ActivityLog, lid2) is None