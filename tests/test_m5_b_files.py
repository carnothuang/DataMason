"""M5-B1 附件后端验收（0.11.0 DR-28/DR-33）。

- 上传/下载/预览/删除全通；文本可内嵌预览、不支持预览类型 415
- 扩展名白名单拒绝、非成员 403
- 操作员按行可见性：他人记录的附件不可下载（数据范围）
- 删除幂等
"""
from __future__ import annotations

import io

import pytest

from server.auth.service import hash_password
from server.models import DeploymentMember, DeploymentMenu, User

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


def _upload(client, did, name="a.txt", content=b"hello", **form):
    return client.post(
        f"/api/v1/app/{did}/api/files",
        data={"file": (io.BytesIO(content), name), **form},
        content_type="multipart/form-data",
    )


def test_upload_download_preview_delete(app, env, patch_llm):
    c = env["client"]
    did = _deploy(c)
    up = _upload(c, did, "note.txt", b"hello world")
    assert up.status_code == 201, up.get_data(as_text=True)
    info = up.get_json()
    assert info["filename"] == "note.txt" and info["size"] == 11

    dl = c.get(f"/api/v1/app/{did}/api/files/{info['token']}")
    assert dl.status_code == 200 and dl.data == b"hello world"
    pv = c.get(f"/api/v1/app/{did}/api/files/{info['token']}/preview")
    assert pv.status_code == 200 and pv.data == b"hello world"
    assert pv.headers.get("X-Content-Type-Options") == "nosniff"

    # 不支持预览的类型 → 415
    bin_up = _upload(c, did, "archive.zip", b"PK\x03\x04")
    assert bin_up.status_code == 201
    assert c.get(f"/api/v1/app/{did}/api/files/{bin_up.get_json()['token']}/preview").status_code == 415

    assert c.delete(f"/api/v1/app/{did}/api/files/{info['token']}").status_code == 200
    assert c.get(f"/api/v1/app/{did}/api/files/{info['token']}").status_code == 404
    # 幂等
    assert c.delete(f"/api/v1/app/{did}/api/files/{info['token']}").status_code == 404


def test_extension_whitelist_and_non_member(app, env, patch_llm):
    c = env["client"]
    did = _deploy(c)
    bad = _upload(c, did, "evil.exe", b"MZ")
    assert bad.status_code == 400

    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        s.add(User(username="outsider", password_hash=hash_password("pw")))
        s.commit()
    out = app.test_client()
    out.post("/api/v1/auth/login", json={"username": "outsider", "password": "pw"})
    assert _upload(out, did, "a.txt").status_code == 403


def test_operator_cannot_download_others_attachment(app, env, patch_llm):
    c = env["client"]
    did = _deploy(c)
    # 创建人行 + 其附件
    row = c.post(f"/api/v1/app/{did}/api/tables/customers/rows", json={"name": "老猫客户"}).get_json()
    up = _upload(c, did, "a.txt", b"secret",
                 table="customers", row_pk=str(row["id"]), field="note")
    assert up.status_code == 201
    token = up.get_json()["token"]

    # 操作员获授权（默认 own 数据范围），看不到创建人的记录 → 附件 403
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        uid = User(username="op", password_hash=hash_password("pw"))
        s.add(uid)
        s.commit()
        op_id = uid.id
        menu = s.query(DeploymentMenu).filter_by(deployment_id=did).first()
        s.add(DeploymentMember(deployment_id=did, user_id=op_id, role="operator"))
        from server.models import DeploymentMenuGrant
        s.add(DeploymentMenuGrant(menu_id=menu.id, user_id=op_id))
        s.commit()
    op = app.test_client()
    op.post("/api/v1/auth/login", json={"username": "op", "password": "pw"})
    assert op.get(f"/api/v1/app/{did}/api/files/{token}").status_code == 403
