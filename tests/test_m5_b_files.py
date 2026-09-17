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


# ---------------- 独立验收补验 T-4/T-5/T-6/T-8 + D-1/D-2/D-3 ----------------

def test_cross_deployment_isolation(app, env, patch_llm):
    """T-4：异部署不可取他人附件（404）。"""
    c = env["client"]
    did1 = _deploy(c)
    did2 = _deploy(c)
    token = _upload(c, did1, "a.txt", b"x").get_json()["token"]
    assert c.get(f"/api/v1/app/{did1}/api/files/{token}").status_code == 200
    assert c.get(f"/api/v1/app/{did2}/api/files/{token}").status_code == 404


def test_path_traversal_rejected_and_filename_safe(app, env, patch_llm):
    """T-5：穿越 token 404；上传文件名带 ../ 仍按随机 token 安全落盘。"""
    c = env["client"]
    did = _deploy(c)
    for bad in ["..%2F..%2Fmeta.db", "....//....//meta.db", "%2e%2e%2f%2e%2e%2fmeta.db"]:
        assert c.get(f"/api/v1/app/{did}/api/files/{bad}").status_code == 404
    up = _upload(c, did, "../../evil.txt", b"safe")
    assert up.status_code == 201
    token = up.get_json()["token"]
    assert ".." not in token
    dl = c.get(f"/api/v1/app/{did}/api/files/{token}")
    assert dl.status_code == 200 and dl.data == b"safe"


def test_oversize_upload_rejected(app, env, patch_llm):
    """T-6：超单文件上限 400。"""
    c = env["client"]
    did = _deploy(c)
    app.config["DM_UPLOAD_MAX_FILE_BYTES"] = 5
    resp = _upload(c, did, "a.txt", b"0123456789")
    assert resp.status_code == 400
    assert "上限" in resp.get_json()["error"]


def test_api_key_cannot_reach_files(app, env, patch_llm):
    """T-8：API key 通道不可触达文件接口（403，DR-33）。"""
    c = env["client"]
    did = _deploy(c)
    token = _upload(c, did, "a.txt", b"x").get_json()["token"]
    key = c.post(f"/api/v1/deployments/{did}/api-keys", json={"name": "k"}).get_json()["key"]
    resp = c.get(f"/api/v1/app/{did}/api/files/{token}", headers={"X-API-Key": key})
    assert resp.status_code == 403


def test_destroy_cleans_attachments_and_files_dir(app, env, patch_llm):
    """D-1：销毁级联清 attachments 行与附件目录。"""
    from pathlib import Path
    from server.models import Attachment
    c = env["client"]
    did = _deploy(c)
    assert _upload(c, did, "a.txt", b"hello").status_code == 201
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        assert s.query(Attachment).filter_by(deployment_id=did).count() == 1
    files_dir = Path(app.config["DATA_ROOT"]) / "deployments" / f"deployment_{did}.files"
    assert files_dir.exists()

    resp = c.post(f"/api/v1/deployments/{did}/destroy")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json().get("files_dir_removed") is True
    with Session() as s:
        assert s.query(Attachment).filter_by(deployment_id=did).count() == 0
    assert not files_dir.exists()


def test_mime_binding_blocks_html_and_svg_inline(app, env, patch_llm):
    """D-2：忽略客户端 MIME，按扩展名绑定；html/svg 不可 inline 预览。"""
    c = env["client"]
    did = _deploy(c)
    # 伪造 Content-Type=text/html 的 .txt
    forged = c.post(
        f"/api/v1/app/{did}/api/files",
        data={"file": (io.BytesIO(b"<script>alert(1)</script>"), "note.txt", "text/html")},
        content_type="multipart/form-data",
    )
    assert forged.status_code == 201
    info = forged.get_json()
    assert info["mime"] == "text/plain"  # 服务端按扩展名绑定，忽略客户端
    pv = c.get(f"/api/v1/app/{did}/api/files/{info['token']}/preview")
    assert pv.status_code == 200
    assert pv.headers.get("Content-Type", "").startswith("text/plain")
    assert "sandbox" in pv.headers.get("Content-Security-Policy", "")

    # SVG：可上传但不可 inline 预览
    svg = c.post(
        f"/api/v1/app/{did}/api/files",
        data={"file": (io.BytesIO(b"<svg onload=alert(1)></svg>"), "x.svg", "image/svg+xml")},
        content_type="multipart/form-data",
    )
    assert svg.status_code == 201
    tok = svg.get_json()["token"]
    assert c.get(f"/api/v1/app/{did}/api/files/{tok}/preview").status_code == 415


def test_stopped_deployment_blocks_files(app, env, patch_llm):
    """D-3：停止态文件接口 409。"""
    c = env["client"]
    did = _deploy(c)
    assert c.post(f"/api/v1/deployments/{did}/stop").status_code == 200
    assert _upload(c, did, "a.txt").status_code == 409


def test_mime_reject_happens_before_write(app, env, patch_llm):
    """P3-1：MIME 白名单不通过时不得落盘（无孤儿文件）。"""
    from pathlib import Path
    c = env["client"]
    did = _deploy(c)
    app.config["DM_UPLOAD_ALLOWED_MIME"] = "image/png"  # 收紧：.txt 不在允许 MIME 内
    resp = _upload(c, did, "a.txt", b"x")
    assert resp.status_code == 400
    files_dir = Path(app.config["DATA_ROOT"]) / "deployments" / f"deployment_{did}.files"
    leftovers = list(files_dir.glob("*")) if files_dir.exists() else []
    assert leftovers == []
