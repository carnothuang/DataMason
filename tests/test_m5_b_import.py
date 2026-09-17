"""M5-B3/B5 导入导出验收（0.11.0 DR-29/DR-32）。

- CSV 导入预览：自动映射、错误清单、行数统计
- strict 模式含错整体回滚；skip_errors 只导正确行
- 导入日志聚合为 1 条 action=import
- 模板下载、XLSX 导出
"""
from __future__ import annotations

import io

import pytest
from openpyxl import load_workbook

from server.auth.service import hash_password
from server.models import ActivityLog, User

SPEC = {
    "biz_notes": "客户",
    "tables": [
        {"name": "customers", "biz_name": "客户", "columns": [
            {"name": "id", "biz_name": "主键", "type": "INTEGER", "is_pk": True,
             "autoincrement": True, "nullable": False},
            {"name": "name", "biz_name": "客户名", "type": "VARCHAR",
             "length": 32, "nullable": False, "unique": True},
            {"name": "age", "biz_name": "年龄", "type": "INTEGER"},
        ], "indexes": []},
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
    prev = client.post("/api/v1/deployments/preview", json={"description": "客户"}).get_json()
    return client.post("/api/v1/deployments/confirm", json={
        "spec_json": prev["spec_json"], "biz_name": "客户", "seed_data": False,
    }).get_json()["deployment_id"]


def _csv(rows: list[str]) -> bytes:
    return ("\n".join(rows) + "\n").encode("utf-8")


def _preview(client, did, table, rows):
    return client.post(
        f"/api/v1/app/{did}/api/tables/{table}/import/preview",
        data={"file": (io.BytesIO(_csv(rows)), "data.csv")},
        content_type="multipart/form-data",
    )


def test_import_preview_and_commit(app, env, patch_llm):
    c = env["client"]
    did = _deploy(c)
    base = f"/api/v1/app/{did}/api/tables/customers"

    pv = _preview(c, did, "customers", ["客户名,年龄", "张三,30", "李四,25"])
    assert pv.status_code == 200, pv.get_data(as_text=True)
    info = pv.get_json()
    assert info["total"] == 2 and info["error_count"] == 0
    assert info["mapping"]["name"] == 0

    commit = c.post(f"{base}/import/commit", json={
        "upload_token": info["upload_token"], "mapping": info["mapping"], "mode": "strict",
    })
    assert commit.status_code == 200, commit.get_data(as_text=True)
    assert commit.get_json()["inserted"] == 2

    rows = c.get(f"{base}/rows").get_json()
    assert rows["total"] == 2

    # 导入日志聚合为 1 条
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        logs = s.query(ActivityLog).filter_by(deployment_id=did, action="import").all()
        assert len(logs) == 1
        assert logs[0].changes.get("inserted") == 2


def test_import_strict_rollback_and_skip_errors(app, env, patch_llm):
    c = env["client"]
    did = _deploy(c)
    base = f"/api/v1/app/{did}/api/tables/customers"
    # 第二行 name 为空（必填）→ 有错误
    pv = _preview(c, did, "customers", ["客户名,年龄", "张三,30", ",25"])
    info = pv.get_json()
    assert info["error_count"] >= 1

    strict = c.post(f"{base}/import/commit", json={
        "upload_token": info["upload_token"], "mapping": info["mapping"], "mode": "strict",
    })
    assert strict.status_code == 400
    assert c.get(f"{base}/rows").get_json()["total"] == 0

    skip = c.post(f"{base}/import/commit", json={
        "upload_token": info["upload_token"], "mapping": info["mapping"], "mode": "skip_errors",
    })
    assert skip.status_code == 200, skip.get_data(as_text=True)
    assert skip.get_json()["inserted"] == 1
    assert c.get(f"{base}/rows").get_json()["total"] == 1


def test_import_template_and_xlsx_export(app, env, patch_llm):
    c = env["client"]
    did = _deploy(c)
    base = f"/api/v1/app/{did}/api/tables/customers"
    c.post(f"{base}/rows", json={"name": "王五", "age": 40})

    tmpl = c.get(f"{base}/import/template")
    assert tmpl.status_code == 200
    wb = load_workbook(io.BytesIO(tmpl.data))
    header = [c.value for c in wb.active[1]]
    assert "客户名" in header and "主键" not in header

    xls = c.get(f"{base}/rows.xlsx")
    assert xls.status_code == 200, xls.get_data(as_text=True)
    wb2 = load_workbook(io.BytesIO(xls.data))
    data = [[c.value for c in r] for r in wb2.active.iter_rows()]
    assert "name" in data[0]
    assert any("王五" in [str(x) for x in r] for r in data[1:])
