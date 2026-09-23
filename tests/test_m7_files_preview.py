"""附件预览修复（0.13.0 补充）：DATA_ROOT 锚定 + 缺失文件返回 404（非 500）。

根因：DATA_ROOT 默认 `./data` 相对进程 CWD 解析，从不同目录启动时上传与读取落在
不同 data/ 目录，导致附件预览 FileNotFoundError。
"""
from __future__ import annotations

from server.app import anchor_path, anchor_sqlite_uri


def test_anchor_path_relative(tmp_path):
    root = str(tmp_path)
    assert anchor_path(root, "./data") == str((tmp_path / "data").resolve())
    assert anchor_path(root, "data") == str((tmp_path / "data").resolve())
    # 绝对路径原样返回
    abs_path = str(tmp_path / "data")
    assert anchor_path(root, abs_path) == abs_path


def test_anchor_sqlite_uri_relative(tmp_path):
    root = str(tmp_path)
    got = anchor_sqlite_uri(root, "sqlite:///./datamason_meta.db")
    assert got.startswith("sqlite:///")
    assert got == "sqlite:///" + (tmp_path / "datamason_meta.db").resolve().as_posix()


def test_anchor_sqlite_uri_absolute_and_other(tmp_path):
    root = str(tmp_path)
    abs_uri = "sqlite:///" + (tmp_path / "meta.db").as_posix()
    assert anchor_sqlite_uri(root, abs_uri) == abs_uri
    assert anchor_sqlite_uri(root, "postgresql://u@h/db") == "postgresql://u@h/db"
    assert anchor_sqlite_uri(root, "sqlite://") == "sqlite://"


def test_default_app_config_paths_are_absolute(app):
    """默认配置（相对路径）经 create_app 后 DATA_ROOT 必须为绝对路径。"""
    from pathlib import Path

    assert Path(app.config["DATA_ROOT"]).is_absolute()


def test_preview_missing_file_returns_404(app):
    """附件记录存在但磁盘文件缺失 → 404（可读提示），不得 500。"""
    from server.auth.service import hash_password
    from server.models import Attachment, Deployment, User

    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        u = User(username="laomao", password_hash=hash_password("pw"),
                 is_platform_admin=True)
        s.add(u)
        s.flush()
        d = Deployment(biz_name="预览验证", owner_id=u.id, status="running")
        s.add(d)
        s.flush()
        s.add(Attachment(deployment_id=d.id, token="missingtoken", filename="a.png",
                         mime="image/png", size=10, storage_rel="deployments/nope/a.png",
                         status="active"))
        s.commit()
        did = d.id

    c = app.test_client()
    assert c.post("/api/v1/auth/login",
                  json={"username": "laomao", "password": "pw"}).status_code == 200
    r = c.get(f"/api/v1/app/{did}/api/files/missingtoken/preview")
    assert r.status_code == 404
    assert "丢失" in r.get_json()["error"]
    r2 = c.get(f"/api/v1/app/{did}/api/files/missingtoken")
    assert r2.status_code == 404
