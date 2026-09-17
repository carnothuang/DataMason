"""独立验收探针（一次性）：验证 0.11.0 计划/报告中「已声明但测试未覆盖」的行为。

运行后即删除；不作为项目测试资产。
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from server.auth.service import hash_password
from server.models import (
    Attachment,
    Deployment,
    DeploymentMenu,
    DeploymentMenuGrant,
    DeploymentMember,
    FlowSpec,
    PageSpec,
    TableSpec,
    User,
)

SPEC = {
    "biz_notes": "进销存",
    "tables": [
        {
            "name": "customers", "biz_name": "客户",
            "columns": [
                {"name": "id", "biz_name": "主键", "type": "INTEGER",
                 "is_pk": True, "autoincrement": True, "nullable": False},
                {"name": "name", "biz_name": "客户名", "type": "VARCHAR",
                 "length": 64, "nullable": False},
            ],
            "indexes": [],
        },
        {
            "name": "orders", "biz_name": "订单",
            "columns": [
                {"name": "id", "biz_name": "主键", "type": "INTEGER",
                 "is_pk": True, "autoincrement": True, "nullable": False},
                {"name": "customer_id", "biz_name": "客户", "type": "INTEGER",
                 "nullable": False, "is_fk": True,
                 "ref_table": "customers", "ref_column": "id"},
            ],
            "indexes": [],
        },
    ],
}


class FakeLlm:
    def chat_json(self, system_prompt, user_prompt):
        class _U:
            tokens_in = 1; tokens_out = 1; duration_ms = 1
        return SPEC, _U()


@pytest.fixture()
def patch_llm(monkeypatch):
    monkeypatch.setattr("server.settings.service.get_default_llm_client",
                        lambda _app: FakeLlm())
    yield


@pytest.fixture()
def env(app):
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        s.add(User(username="laomao", password_hash=hash_password("pw"),
                   is_platform_admin=True))
        s.commit()
    c = app.test_client()
    c.post("/api/v1/auth/login", json={"username": "laomao", "password": "pw"})
    return {"client": c}


def _deploy(client) -> int:
    prev = client.post("/api/v1/deployments/preview", json={"description": "进销存"}).get_json()
    return client.post("/api/v1/deployments/confirm", json={
        "spec_json": prev["spec_json"], "biz_name": "进销存", "seed_data": False,
    }).get_json()["deployment_id"]


NEW_TABLE = {
    "name": "visits", "biz_name": "回访",
    "columns": [
        {"name": "id", "type": "INTEGER", "is_pk": True, "autoincrement": True,
         "nullable": False},
        {"name": "customer_id", "type": "INTEGER", "nullable": False, "is_fk": True,
         "ref_table": "customers", "ref_column": "id"},
        {"name": "note", "biz_name": "回访记录", "type": "TEXT"},
    ],
    "indexes": [],
}


# ---------- P1: link 模式必须拒绝（DR-27）—— 计划 M5-A-1 验收项，原测试未覆盖 ----------

def test_p1_link_mode_rejects_ddl(app, env, patch_llm):
    c = env["client"]
    did = _deploy(c)
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:  # 模拟直连模式（外部库）
        d = s.get(Deployment, did)
        d.data_mode = "link"
        s.commit()
    p = f"/api/v1/deployments/{did}/tables/propose"
    r = c.post(p, json={"spec": NEW_TABLE})
    print("P1 propose(link) ->", r.status_code, r.get_json())
    assert r.status_code == 400
    r2 = c.post(f"/api/v1/deployments/{did}/tables/apply", json={"spec": NEW_TABLE})
    print("P1 apply(link)   ->", r2.status_code, r2.get_json())
    assert r2.status_code == 400
    r3 = c.post(f"/api/v1/deployments/{did}/tables/customers/drop")
    print("P1 drop(link)    ->", r3.status_code, r3.get_json())
    assert r3.status_code == 400


# ---------- P2: 被流程引用禁删（DR-31）—— 计划验收项，原测试未覆盖 ----------

def test_p2_flow_reference_blocks_drop(app, env, patch_llm):
    c = env["client"]
    did = _deploy(c)
    p = f"/api/v1/deployments/{did}"
    assert c.post(f"{p}/tables/apply", json={"spec": NEW_TABLE}).status_code == 201

    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        ts = s.query(TableSpec).filter_by(deployment_id=did, name="visits").first()
        page = s.query(PageSpec).filter_by(deployment_id=did, table_id=ts.id).first()
        s.add(FlowSpec(deployment_id=did, name="回访流程",
                       steps_json=json.dumps([{"name": "登记回访", "page_id": page.id}])))
        s.commit()

    info = c.post(f"{p}/tables/visits/drop/propose").get_json()
    print("P2 propose ->", {k: info[k] for k in ("blocked", "flows", "reason")})
    assert info["blocked"] is True and info["flows"]
    r = c.post(f"{p}/tables/visits/drop")
    print("P2 drop    ->", r.status_code, r.get_json())
    assert r.status_code == 400
    # 表仍在
    with Session() as s:
        assert s.query(TableSpec).filter_by(deployment_id=did, name="visits").first() is not None


# ---------- P3: 提案语句 == 执行语句 —— 计划 M5-A-1 验收项，原测试未断言 ----------

def test_p3_propose_statements_equal_apply(app, env, patch_llm):
    c = env["client"]
    did = _deploy(c)
    p = f"/api/v1/deployments/{did}"
    prop = c.post(f"{p}/tables/propose", json={"spec": NEW_TABLE}).get_json()
    applied = c.post(f"{p}/tables/apply", json={"spec": NEW_TABLE}).get_json()
    print("P3 propose stmts:", prop["statements"])
    print("P3 applied stmts:", applied["statements"])
    assert prop["statements"] == applied["statements"]


# ---------- P4: 跨部署隔离 —— 计划验收项「跨部署隔离」，原测试未覆盖 ----------

def test_p4_cross_deployment_isolation(app, env, patch_llm):
    c = env["client"]
    did_a = _deploy(c)
    did_b = _deploy(c)
    up = c.post(f"/api/v1/app/{did_a}/api/files",
                data={"file": (io.BytesIO(b"secret"), "a.txt")},
                content_type="multipart/form-data")
    token = up.get_json()["token"]
    ok = c.get(f"/api/v1/app/{did_a}/api/files/{token}")
    cross = c.get(f"/api/v1/app/{did_b}/api/files/{token}")
    print("P4 same-deploy:", ok.status_code, "| cross-deploy:", cross.status_code)
    assert ok.status_code == 200 and cross.status_code == 404


# ---------- P5: 路径穿越 —— 计划验收项，原测试未覆盖 ----------

def test_p5_path_traversal_blocked(app, env, patch_llm):
    c = env["client"]
    did = _deploy(c)
    for tok in ("../../../../etc/passwd", "..%2F..%2Fmeta.db", "....//....//meta.db"):
        r = c.get(f"/api/v1/app/{did}/api/files/{tok}")
        print("P5", tok, "->", r.status_code)
        assert r.status_code in (404, 400)
    # 上传文件名带穿越亦不得落到目录外
    up = c.post(f"/api/v1/app/{did}/api/files",
                data={"file": (io.BytesIO(b"x"), "../../evil.txt")},
                content_type="multipart/form-data")
    print("P5 upload traversal ->", up.status_code, up.get_json())
    if up.status_code == 201:
        rel = up.get_json()["token"]
        assert "/" not in rel and ".." not in rel


# ---------- P6: 单文件大小上限 —— 计划验收项，原测试未覆盖 ----------

def test_p6_size_limit_rejected(app, env, patch_llm):
    c = env["client"]
    did = _deploy(c)
    limit = app.config["DM_UPLOAD_MAX_FILE_BYTES"]
    big = b"0" * (limit + 1024)
    r = c.post(f"/api/v1/app/{did}/api/files",
               data={"file": (io.BytesIO(big), "big.txt")},
               content_type="multipart/form-data")
    print("P6 over-size ->", r.status_code, r.get_json())
    assert r.status_code == 400


# ---------- P7: 销毁部署级联清附件与文件目录（DR-28/技术方案 §4.4）----------

def test_p7_destroy_cascades_attachment_cleanup(app, env, patch_llm):
    c = env["client"]
    did = _deploy(c)
    up = c.post(f"/api/v1/app/{did}/api/files",
                data={"file": (io.BytesIO(b"keepme"), "a.txt")},
                content_type="multipart/form-data")
    assert up.status_code == 201
    root = Path(app.config["DATA_ROOT"])
    files_dir = root / "deployments" / f"deployment_{did}.files"
    print("P7 files dir exists before:", files_dir.exists(),
          "| contents:", [p.name for p in files_dir.glob("*")] if files_dir.exists() else None)

    r = c.post(f"/api/v1/deployments/{did}/destroy", json={"confirm": "进销存"})
    print("P7 destroy ->", r.status_code, r.get_json())

    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        n_att = s.query(Attachment).filter_by(deployment_id=did).count()
    print("P7 leftover attachments rows:", n_att)
    print("P7 files dir exists after:", files_dir.exists(),
          "| contents:", [p.name for p in files_dir.glob("*")] if files_dir.exists() else None)
    assert n_att == 0, "销毁后仍留有 attachments 元数据行"
    assert not files_dir.exists() or not list(files_dir.glob("*")), "销毁后仍留有附件文件"


# ---------- P8: 必填列未映射应阻断（技术方案 §5.3）----------

def test_p8_unmapped_required_column_blocks(app, env, patch_llm):
    c = env["client"]
    did = _deploy(c)
    base = f"/api/v1/app/{did}/api/tables/customers"
    # 只给「客户名」列，故意不映射必填列 name
    csv = "无关列\n张三\n".encode("utf-8")
    pv = c.post(f"{base}/import/preview",
                data={"file": (io.BytesIO(csv), "d.csv")},
                content_type="multipart/form-data")
    info = pv.get_json()
    print("P8 preview ->", {k: info.get(k) for k in ("mapping", "total", "valid", "error_count")})
    assert info["mapping"] == {}, "本用例预期无自动映射"
    assert info["error_count"] >= 1, "必填列未映射未在预览阶段报错（技术方案要求阻断）"


# ---------- P9: 预览接口的 Content-Type 是否可由客户端伪造 (存储型 XSS 面) ----------

def test_p9_preview_content_type_forgery(app, env, patch_llm):
    c = env["client"]
    did = _deploy(c)
    payload = b"<script>alert(document.cookie)</script>"
    up = c.post(f"/api/v1/app/{did}/api/files",
                data={"file": (io.BytesIO(payload), "note.txt", "text/html")},
                content_type="multipart/form-data")
    assert up.status_code == 201, up.get_data(as_text=True)
    info = up.get_json()
    pv = c.get(f"/api/v1/app/{did}/api/files/{info['token']}/preview")
    print("P9 stored mime:", info["mime"], "| preview status:", pv.status_code,
          "| preview Content-Type:", pv.headers.get("Content-Type"))
    print("P9 nosniff:", pv.headers.get("X-Content-Type-Options"))
    assert pv.status_code == 200
    assert pv.headers.get("Content-Type", "").startswith("text/html"), \
        "若此处非 text/html 则伪造未生效"


# ---------- P10: 无授权操作员不得访问任何表（原报告称修复的越权隐患）----------

def test_p10_operator_without_grants_sees_nothing(app, env, patch_llm):
    c = env["client"]
    did = _deploy(c)
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        u = User(username="nogrant", password_hash=hash_password("pw"))
        s.add(u)
        s.commit()
        s.add(DeploymentMember(deployment_id=did, user_id=u.id, role="operator"))
        s.commit()
    op = app.test_client()
    op.post("/api/v1/auth/login", json={"username": "nogrant", "password": "pw"})
    r = op.get(f"/api/v1/app/{did}/api/tables/customers/rows")
    print("P10 no-grant operator rows ->", r.status_code, r.get_data(as_text=True)[:160])
    assert r.status_code in (403, 404), "无授权操作员仍可访问表数据（越权）"
