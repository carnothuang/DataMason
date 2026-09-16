"""M4-1 验收：问数据后端（NL → 只读 SELECT → 行集 + 一句话结论）。

- schema context 由 table_specs 确定性组装（含主外键/采样值），LLM 生成 SELECT；
- sqlglot 只读门：单条 SELECT、表白名单、禁多语句/DML/DDL/跨库限定名/锁定/INTO；
- 生成失败中文原因回喂重试（≤2）；结论 LLM 失败回退确定性摘要；
- dataaccess 执行 + 外层 LIMIT 兜底（MAX_ROWS）；
- 每次问答写 ai_generations（kind=qa，token/duration 埋点）；
- 纯只读浏览不进 activity_logs；
- 权限：创建人/管理员可用，操作员/非成员 403；未配置 LLM 400。
"""
from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine, text

from server.auth.service import hash_password
from server.llm.client import Usage
from server.models import (
    ActivityLog,
    AiGeneration,
    Connection,
    Deployment,
    DeploymentMember,
    User,
)

SCHEMA = {
    "biz_notes": "留言板：收集访客反馈",
    "tables": [
        {
            "name": "messages",
            "biz_name": "留言",
            "columns": [
                {"name": "id", "biz_name": "主键", "type": "INTEGER",
                 "is_pk": True, "autoincrement": True, "nullable": False},
                {"name": "name", "biz_name": "姓名", "type": "VARCHAR",
                 "length": 64, "nullable": False},
                {"name": "amount", "biz_name": "金额", "type": "INTEGER", "nullable": True},
                {"name": "status", "biz_name": "状态", "type": "VARCHAR",
                 "length": 16, "nullable": True},
            ],
            "indexes": [],
        }
    ],
}


class FakeLlm:
    """按系统提示词分派：结构生成 / 只读 SQL / 一句话结论。"""

    def __init__(self):
        self.sql_queue: list[str] = []
        self.title = "留言统计"
        self.conclusion = "共 10 条留言。"
        self.fail_conclusion = False
        self.chart = None

    def chat_json(self, system_prompt, user_prompt):
        if "只读 SQL" in system_prompt:
            sql = self.sql_queue.pop(0) if self.sql_queue else "SELECT id, name, amount FROM messages"
            return {"sql": sql, "title": self.title, "chart": self.chart}, Usage(
                tokens_in=7, tokens_out=5, duration_ms=3)
        if "总结" in system_prompt:
            if self.fail_conclusion:
                raise RuntimeError("结论生成失败")
            return {"conclusion": self.conclusion}, Usage(tokens_in=3, tokens_out=2, duration_ms=1)
        return SCHEMA, Usage(tokens_in=11, tokens_out=13, duration_ms=9)


@pytest.fixture()
def fake_llm(monkeypatch):
    inst = FakeLlm()
    monkeypatch.setattr(
        "server.settings.service.get_default_llm_client",
        lambda _app: inst,
    )
    return inst


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


def _session(app):
    return app.extensions["datamason_session_factory"]()


def _mk_user(app, username: str) -> int:
    with _session(app) as s:
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


def _deploy(client, seed: bool = True) -> int:
    preview = client.post("/api/v1/deployments/preview",
                          json={"description": "留言板"}).get_json()
    result = client.post("/api/v1/deployments/confirm", json={
        "spec_json": preview["spec_json"],
        "biz_name": "留言演示",
        "seed_data": seed,
    }).get_json()
    return result["deployment_id"]


def _biz_engine(app, did: int):
    with _session(app) as s:
        d = s.get(Deployment, did)
        c = s.get(Connection, d.connection_id)
        uri = app.extensions["cipher"].decrypt(c.conn_uri)
    return create_engine(uri, future=True)


def _insert_rows(app, did: int, n: int) -> None:
    engine = _biz_engine(app, did)
    try:
        now = datetime.now().isoformat(sep=" ")
        with engine.begin() as conn:
            for i in range(n):
                conn.execute(
                    text("INSERT INTO messages (name, amount, status, created_by,"
                         " created_at, updated_by, updated_at) VALUES (:n, :a, :s, 1,"
                         " :t, 1, :t)"),
                    {"n": f"访客{i}", "a": i, "s": "已处理", "t": now},
                )
    finally:
        engine.dispose()


def _ask(client, did: int, question: str = "有多少留言？"):
    return client.post(f"/api/v1/app/{did}/qa/ask", json={"question": question})


def _row_total(client, did: int) -> int:
    return client.get(
        f"/api/v1/app/{did}/api/tables/messages/rows?page_size=1").get_json()["total"]


# ---------------- 用例 ----------------

def test_qa_ask_returns_rows_and_records_generation(app, env, fake_llm):
    did = _deploy(env["client"], seed=True)
    fake_llm.sql_queue = ["SELECT id, name, amount FROM messages ORDER BY id"]
    fake_llm.conclusion = "共 10 条留言。"

    resp = _ask(env["client"], did)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["row_count"] == 10
    assert body["columns"] == ["id", "name", "amount"]
    assert body["conclusion"] == "共 10 条留言。"
    assert body["title"] == "留言统计"
    assert body["attempts"] == 1
    assert body["row_limit"] == 200
    assert body["sql"].lower().startswith("select")

    with _session(app) as s:
        gen = (s.query(AiGeneration).filter_by(kind="qa")
               .order_by(AiGeneration.id.desc()).first())
        assert gen is not None and gen.status == "ok"
        assert gen.tokens_in == 10 and gen.tokens_out == 7 and gen.duration_ms == 4
        assert "有多少留言" in gen.prompt_summary
        assert "10 行" in gen.result_summary


def test_qa_readonly_gate_rejects_and_keeps_data(app, env, fake_llm):
    did = _deploy(env["client"], seed=True)
    bad_cases = [
        "UPDATE messages SET name='x'",
        "DELETE FROM messages",
        "INSERT INTO messages (name) VALUES ('x')",
        "DROP TABLE messages",
        "SELECT 1; DROP TABLE messages",
        "SELECT * FROM no_such_table",
        "SELECT * FROM other.messages",
        "PRAGMA table_info(messages)",
        "SELECT * FROM messages FOR UPDATE",
        "SELECT * INTO copied FROM messages",
        "SELECT load_file('/etc/passwd') FROM messages",
    ]
    for bad in bad_cases:
        fake_llm.sql_queue = [bad, bad, bad]  # 重试也用同样的坏 SQL
        resp = _ask(env["client"], did)
        assert resp.status_code == 400, (bad, resp.get_data(as_text=True))
        assert "只读门" in resp.get_json()["error"], (bad, resp.get_json())

    # 业务数据零变化（写操作未执行）
    assert _row_total(env["client"], did) == 10
    with _session(app) as s:
        err = (s.query(AiGeneration)
               .filter(AiGeneration.kind == "qa", AiGeneration.status == "error")
               .order_by(AiGeneration.id.desc()).first())
        assert err is not None


def test_qa_execution_failure_recorded(app, env, fake_llm, monkeypatch):
    """生成成功但执行失败时也写 ai_generations（status=error），不留空档。"""
    did = _deploy(env["client"], seed=True)
    fake_llm.sql_queue = ["SELECT id, name FROM messages"]

    def _boom(*_a, **_k):
        raise RuntimeError("模拟执行失败")

    monkeypatch.setattr("server.qa.service._execute", _boom)
    resp = _ask(env["client"], did)
    assert resp.status_code == 400
    with _session(app) as s:
        err = (s.query(AiGeneration)
               .filter(AiGeneration.kind == "qa", AiGeneration.status == "error")
               .order_by(AiGeneration.id.desc()).first())
        assert err is not None and "执行" in err.result_summary


def test_validate_chart_structure():
    """E2：图表规格纯函数离线单测（类型白名单 / x,y 必须是结果列 / 不同列）。"""
    from server.qa.service import _validate_chart

    cols = ["vip", "cnt", "name"]
    assert _validate_chart({"type": "pie", "x": "vip", "y": "cnt"}, cols) == {
        "type": "pie", "x": "vip", "y": "cnt"}
    assert _validate_chart(None, cols) is None
    assert _validate_chart({"type": "radar", "x": "vip", "y": "cnt"}, cols) is None
    assert _validate_chart({"type": "bar", "x": "vip"}, cols) is None
    assert _validate_chart({"type": "bar", "x": "vip", "y": "vip"}, cols) is None
    assert _validate_chart({"type": "bar", "x": "ghost", "y": "cnt"}, cols) is None


def test_qa_retry_on_invalid_sql_then_success(app, env, fake_llm):
    did = _deploy(env["client"], seed=True)
    fake_llm.sql_queue = ["SELECT * FROM no_such_table", "SELECT id FROM messages"]
    resp = _ask(env["client"], did)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["attempts"] == 2
    assert resp.get_json()["columns"] == ["id"]


def test_qa_row_limit_cap(app, env, fake_llm):
    did = _deploy(env["client"], seed=False)
    _insert_rows(app, did, 250)
    fake_llm.sql_queue = ["SELECT * FROM messages"]
    resp = _ask(env["client"], did)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["row_count"] == 200  # 外层 LIMIT 兜底


def test_qa_conclusion_falls_back_deterministically(app, env, fake_llm):
    did = _deploy(env["client"], seed=True)
    fake_llm.sql_queue = ["SELECT id FROM messages"]
    fake_llm.fail_conclusion = True
    resp = _ask(env["client"], did)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["conclusion"] == "共 10 行数据。"


def test_qa_chart_spec_validated(app, env, fake_llm):
    did = _deploy(env["client"], seed=True)

    # 合法规格（x/y 均为结果列且不同）→ 原样返回
    fake_llm.sql_queue = ["SELECT name, amount FROM messages"]
    fake_llm.chart = {"type": "bar", "x": "name", "y": "amount"}
    body = _ask(env["client"], did).get_json()
    assert body["chart"] == {"type": "bar", "x": "name", "y": "amount"}, body["chart"]

    # 非法规格：类型不在白名单 / x 不存在 / x==y → 归一为 null（前端退化为表格）
    for bad in (
        {"type": "radar", "x": "name", "y": "amount"},
        {"type": "pie", "x": "nope", "y": "amount"},
        {"type": "bar", "x": "name", "y": "name"},
        {"type": "bar", "x": "name"},
        "not-a-dict",
    ):
        fake_llm.sql_queue = ["SELECT name, amount FROM messages"]
        fake_llm.chart = bad
        resp = _ask(env["client"], did)
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json()["chart"] is None, (bad, resp.get_json()["chart"])


def test_qa_permissions(app, env, fake_llm):
    did = _deploy(env["client"], seed=True)
    fake_llm.sql_queue = ["SELECT id FROM messages"]

    op_id = _mk_user(app, "op")
    adm_id = _mk_user(app, "adm")
    out_id = _mk_user(app, "outsider")
    with _session(app) as s:
        s.add(DeploymentMember(deployment_id=did, user_id=op_id, role="operator"))
        s.add(DeploymentMember(deployment_id=did, user_id=adm_id, role="admin"))
        s.commit()

    # 操作员默认不开放问数据
    fake_llm.sql_queue = ["SELECT id FROM messages"]
    assert _ask(_login(app, "op"), did).status_code == 403
    # 非成员 403
    fake_llm.sql_queue = ["SELECT id FROM messages"]
    assert _ask(_login(app, "outsider"), did).status_code == 403
    # 管理员可用
    fake_llm.sql_queue = ["SELECT id FROM messages"]
    resp = _ask(_login(app, "adm"), did)
    assert resp.status_code == 200, resp.get_data(as_text=True)


def test_qa_not_logged_into_activity_logs(app, env, fake_llm):
    did = _deploy(env["client"], seed=True)
    with _session(app) as s:
        before = s.query(ActivityLog).filter_by(deployment_id=did).count()
    fake_llm.sql_queue = ["SELECT id FROM messages"]
    assert _ask(env["client"], did).status_code == 200
    with _session(app) as s:
        after = s.query(ActivityLog).filter_by(deployment_id=did).count()
    assert after == before


def test_qa_requires_configured_llm(app, env, fake_llm, monkeypatch):
    did = _deploy(env["client"], seed=True)
    monkeypatch.setattr(
        "server.settings.service.get_default_llm_client", lambda _app: None)
    resp = _ask(env["client"], did)
    assert resp.status_code == 400
    assert "尚未配置默认 LLM" in resp.get_json()["error"]


def test_qa_gate_unit_and_wrap_limit():
    from server.qa import service as qa_service

    assert qa_service._wrap_limit("SELECT 1") == "SELECT * FROM (SELECT 1) AS _dm_qa LIMIT 200"
    # CTE 中的表名不算白名单来源
    sql = qa_service.enforce_readonly(
        "WITH x AS (SELECT id FROM messages) SELECT * FROM x",
        {"messages"}, "sqlite")
    assert "messages" in sql and "x" in sql
    with pytest.raises(qa_service.QaError):
        qa_service.enforce_readonly("SELECT * FROM other_table", {"messages"}, "sqlite")
