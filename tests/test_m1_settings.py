"""M1 配置中心验收：多模型 / 连接 / Redis。

- 凭证加密落库、读取掩码、更新时掩码保持原值
- SQLite 连接直建 + 连接测试
- Redis 配置保存/读取；真实 ping 仅在本机 Redis 可达时断言
- 真实模型 test 需 DATAMASON_RUN_LLM=1 与 LLM 环境变量
"""
from __future__ import annotations

import json
import os

import pytest

from server.models import Connection, LlmModel
from server.settings import service


def _s(app):
    return app.extensions["datamason_session_factory"]()


@pytest.fixture(autouse=True)
def _login_admin(app, client):
    """M2-1 起设置接口需平台管理员登录；本文件全部用例共享登录态。"""
    from server.auth.service import hash_password
    from server.models import User

    with _s(app) as s:
        s.add(User(username="laomao", display_name="老猫",
                   password_hash=hash_password("pw"), is_platform_admin=True))
        s.commit()
    resp = client.post("/api/v1/auth/login",
                       json={"username": "laomao", "password": "pw"})
    assert resp.status_code == 200


# ---------- 多模型 ----------

def test_model_crud_and_masking(app, client):
    # 创建
    resp = client.post("/api/v1/settings/models", json={
        "name": "火山方舟",
        "base_url": "https://ark.cn-beijing.volces.com/api/coding/v3",
        "model_name": "ark-code-latest",
        "api_key": "ark-secret-123",
    })
    assert resp.status_code == 201, resp.get_data(as_text=True)
    mid = resp.get_json()["id"]
    assert resp.get_json()["is_default"] is True  # 首个自动默认
    assert resp.get_json()["has_api_key"] is True
    assert resp.get_json()["api_key"] == "********"

    # 落库必须是密文，不含明文 key
    with _s(app) as s:
        stored = s.get(LlmModel, mid).api_key_cipher
    assert "ark-secret-123" not in stored and stored

    # 列表掩码
    listed = client.get("/api/v1/settings/models").get_json()
    assert len(listed) == 1 and listed[0]["api_key"] == "********"

    # 更新：掩码 key 表示不变，其它字段可改
    upd = client.put(f"/api/v1/settings/models/{mid}", json={
        "name": "方舟改名", "api_key": "********",
    })
    assert upd.status_code == 200
    with _s(app) as s:
        assert s.get(LlmModel, mid).api_key_cipher == stored  # 未变
        assert s.get(LlmModel, mid).name == "方舟改名"

    # 更新：传新 key 则替换
    client.put(f"/api/v1/settings/models/{mid}", json={"api_key": "new-key-999"})
    cipher = app.extensions["cipher"]
    with _s(app) as s:
        assert cipher.decrypt(s.get(LlmModel, mid).api_key_cipher) == "new-key-999"

    # 第二个模型 + 设默认
    second = client.post("/api/v1/settings/models", json={
        "name": "备选", "base_url": "https://x.test/v1", "model_name": "m2",
        "api_key": "k",
    }).get_json()
    assert second["is_default"] is False
    client.post(f"/api/v1/settings/models/{second['id']}/default")
    defaults = [m for m in client.get("/api/v1/settings/models").get_json()
                if m["is_default"]]
    assert len(defaults) == 1 and defaults[0]["id"] == second["id"]

    # 删除
    assert client.delete(f"/api/v1/settings/models/{mid}").status_code == 200
    assert client.delete(f"/api/v1/settings/models/999").status_code == 404


def test_model_requires_fields(app, client):
    resp = client.post("/api/v1/settings/models", json={"name": "x"})
    assert resp.status_code == 400


# ---------- 数据连接 ----------

def test_sqlite_connection_create_and_test(app, client):
    resp = client.post("/api/v1/settings/connections", json={
        "name": "本地业务库",
        "dialect": "sqlite",
        "db_path": "biz/demo.db",
        "create": True,
    })
    assert resp.status_code == 201, resp.get_data(as_text=True)
    cid = resp.get_json()["id"]
    assert "conn_uri" not in resp.get_json()  # 不回传连接串

    # 密文落库
    with _s(app) as s:
        enc = s.get(Connection, cid).conn_uri
    assert "demo.db" not in enc or enc.startswith("gAAAA")  # Fernet token

    # 连接测试 → ok
    tested = client.post(f"/api/v1/settings/connections/{cid}/test").get_json()
    assert tested["ok"] is True, tested
    assert tested["status"] == "ok"

    listed = client.get("/api/v1/settings/connections").get_json()
    assert listed[0]["dialect"] == "sqlite" and listed[0]["status"] == "ok"

    assert client.delete(f"/api/v1/settings/connections/{cid}").status_code == 200


def test_connection_validation(app, client):
    assert client.post("/api/v1/settings/connections",
                       json={"name": "x", "dialect": "oracle"}).status_code == 400
    assert client.post("/api/v1/settings/connections",
                       json={"name": "x", "dialect": "sqlite"}).status_code == 400


# ---------- Redis ----------

def test_redis_config_save_and_mask(app, client):
    saved = client.put("/api/v1/settings/redis", json={
        "enabled": True, "host": "127.0.0.1", "port": 6379, "db": 0,
        "password": "redispw",
    }).get_json()
    assert saved["enabled"] is True
    assert saved["password"] == "********"

    # 再次保存用掩码 → 密码不变
    client.put("/api/v1/settings/redis", json={"enabled": True, "password": "********"})
    raw = service._get_raw_redis(app)
    assert raw["password"] == "redispw"

    got = client.get("/api/v1/settings/redis").get_json()
    assert got["host"] == "127.0.0.1" and got["password"] == "********"


def test_redis_real_ping(app, client):
    client.put("/api/v1/settings/redis", json={
        "enabled": True, "host": "127.0.0.1", "port": 6379, "db": 0,
    })
    result = client.post("/api/v1/settings/redis/test").get_json()
    if not result["ok"]:
        pytest.skip(f"本机 Redis 不可达：{result.get('error')}")
    assert result["ok"] is True
    assert result["version"]


# ---------- 默认 LLM 客户端：设置页模型优先，环境变量回退（M0 路径） ----------

def test_default_llm_client_env_fallback(app, client):
    # 既无模型也无环境变量 → None
    assert service.get_default_llm_client(app) is None

    # 仅环境变量 → 回退构建（A2 修复）
    app.config["DATAMASON_LLM_BASE_URL"] = "http://env.example/v1"
    app.config["DATAMASON_LLM_API_KEY"] = "sk-env"
    app.config["DATAMASON_LLM_MODEL"] = "env-model"
    envc = service.get_default_llm_client(app)
    assert envc is not None
    assert envc.config.model == "env-model"

    # 登记设置页模型后优先取模型
    client.post("/api/v1/settings/models", json={
        "name": "页内模型", "base_url": "http://db.example/v1",
        "model_name": "db-model", "api_key": "sk-db",
    })
    dbc = service.get_default_llm_client(app)
    assert dbc is not None and dbc.config.model == "db-model"


# ---------- 真实模型 test（门控） ----------

def test_real_llm_model_connection(app, client):
    if os.environ.get("DATAMASON_RUN_LLM") != "1":
        pytest.skip("设 DATAMASON_RUN_LLM=1 与 LLM 环境变量后运行")
    resp = client.post("/api/v1/settings/models", json={
        "name": "ark-real",
        "base_url": os.environ["DATAMASON_LLM_BASE_URL"],
        "model_name": os.environ.get("DATAMASON_LLM_MODEL", "ark-code-latest"),
        "api_key": os.environ["DATAMASON_LLM_API_KEY"],
    })
    mid = resp.get_json()["id"]
    result = client.post(f"/api/v1/settings/models/{mid}/test").get_json()
    assert result["ok"] is True, result
