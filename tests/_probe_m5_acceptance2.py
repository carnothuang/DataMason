"""独立验收探针 · 第二轮：状态门控 / API key 通道 / 导出列选择 / SVG 内联预览。"""
from __future__ import annotations

import io

import pytest

from server.auth.service import hash_password
from server.models import Deployment, User

SPEC = {
    "biz_notes": "客户",
    "tables": [
        {"name": "customers", "biz_name": "客户", "columns": [
            {"name": "id", "biz_name": "主键", "type": "INTEGER", "is_pk": True,
             "autoincrement": True, "nullable": False},
            {"name": "name", "biz_name": "客户名", "type": "VARCHAR",
             "length": 64, "nullable": False},
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


# ---------- P11: API key 通道能否触达导入/导出（白名单只列 /api/tables/）----------

def test_p11_api_key_reaches_import_and_export(app, env, patch_llm):
    c = env["client"]
    did = _deploy(c)
    base = f"/api/v1/app/{did}/api/tables/customers"
    key = c.post(f"/api/v1/deployments/{did}/api-keys", json={"name": "k"}).get_json()["key"]
    h = {"X-API-Key": key}

    xls = c.get(f"{base}/rows.xlsx", headers=h)
    print("P11 key -> rows.xlsx:", xls.status_code)

    csv = "客户名\nkey客户\n".encode("utf-8")
    pv = c.post(f"{base}/import/preview", headers=h,
                data={"file": (io.BytesIO(csv), "k.csv")},
                content_type="multipart/form-data")
    print("P11 key -> import/preview:", pv.status_code, pv.get_data(as_text=True)[:120])

    f = c.post(f"/api/v1/app/{did}/api/files", headers=h,
               data={"file": (io.BytesIO(b"x"), "a.txt")},
               content_type="multipart/form-data")
    print("P11 key -> files upload:", f.status_code, f.get_data(as_text=True)[:120])


# ---------- P12: 停止态是否仍开放文件/导入（M3-1 称停止态入口不可操作）----------

def test_p12_stopped_state_gate_on_files_and_import(app, env, patch_llm):
    c = env["client"]
    did = _deploy(c)
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        s.get(Deployment, did).status = "stopped"
        s.commit()

    rows = c.get(f"/api/v1/app/{did}/api/tables/customers/rows")
    print("P12 stopped -> rows:", rows.status_code)
    up = c.post(f"/api/v1/app/{did}/api/files",
                data={"file": (io.BytesIO(b"x"), "a.txt")},
                content_type="multipart/form-data")
    print("P12 stopped -> files upload:", up.status_code, up.get_data(as_text=True)[:100])
    pv = c.post(f"/api/v1/app/{did}/api/tables/customers/import/preview",
                data={"file": (io.BytesIO("客户名\nx\n".encode()), "d.csv")},
                content_type="multipart/form-data")
    print("P12 stopped -> import/preview:", pv.status_code, pv.get_data(as_text=True)[:100])
    xls = c.get(f"/api/v1/app/{did}/api/tables/customers/rows.xlsx")
    print("P12 stopped -> rows.xlsx:", xls.status_code)


# ---------- P13: 导出列选择（技术方案 §5.4 cols=a,b,c）与上限可配置 ----------

def test_p13_export_column_selection(app, env, patch_llm):
    c = env["client"]
    did = _deploy(c)
    base = f"/api/v1/app/{did}/api/tables/customers"
    c.post(f"{base}/rows", json={"name": "张三"})
    from openpyxl import load_workbook
    xls = c.get(f"{base}/rows.xlsx?cols=name")
    wb = load_workbook(io.BytesIO(xls.data))
    header = [cc.value for cc in wb.active[1]]
    print("P13 rows.xlsx?cols=name -> header:", header)
    assert header == ["name"], "导出列选择未生效（技术方案 §5.4 的 cols 参数未实现）"


# ---------- P14: SVG 内联预览（白名单含 .svg）----------

def test_p14_svg_inline_preview(app, env, patch_llm):
    c = env["client"]
    did = _deploy(c)
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    up = c.post(f"/api/v1/app/{did}/api/files",
                data={"file": (io.BytesIO(svg), "pic.svg")},
                content_type="multipart/form-data")
    print("P14 svg upload:", up.status_code)
    info = up.get_json()
    pv = c.get(f"/api/v1/app/{did}/api/files/{info['token']}/preview")
    print("P14 svg mime:", info["mime"], "| preview:", pv.status_code,
          "| CT:", pv.headers.get("Content-Type"))
    assert pv.status_code == 200, "SVG 被拒绝内联预览（则此前 XSS 面收窄）"
