"""pytest 公共夹具：临时元数据库的 Flask 应用。"""
from __future__ import annotations

import pytest

from server.app import create_app
from server.config import Config


@pytest.fixture()
def app(tmp_path):
    db_path = tmp_path / "meta.db"
    cfg = Config(
        meta_db_uri=f"sqlite:///{db_path.as_posix()}",
        data_root=str(tmp_path / "data"),
    )
    application = create_app(cfg)
    application.testing = True
    return application


@pytest.fixture()
def client(app):
    return app.test_client()
