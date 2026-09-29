"""M0-5 验收：LLM 网关 + 一句话→spec→建表→演示数据→浏览 裸链路（API 层，无前端）。

真实 LLM 部分：用 httpx MockTransport 模拟 OpenAI 兼容响应，验证协议契约与
JSON 解析；无密钥环境不触网。真 LLM 联调由 M1 验收（真留言板场景≤3分钟）执行。
"""
from __future__ import annotations

import json

import httpx
import pytest
from flask import Flask
from sqlalchemy import text

from datamason.crud.engine import CrudRegistry, create_crud_blueprint
from datamason.dataaccess.manager import execute_ddl, make_engine
from datamason.ddl.ddl_builder import build_schema_ddl
from datamason.ddl.table_spec import SchemaSpec
from datamason.llm.client import LlmConfig, LlmNotConfiguredError, OpenAiCompatibleClient
from datamason.llm.structure import generate_schema, generate_seed_rows

GUESTBOOK_LLM_JSON = {
    "biz_notes": "访客留言收集",
    "tables": [
        {
            "name": "messages",
            "biz_name": "留言",
            "biz_notes": "每条访客留言",
            "columns": [
                {"name": "id", "biz_name": "主键", "type": "INTEGER",
                 "is_pk": True, "autoincrement": True, "nullable": False},
                {"name": "visitor", "biz_name": "访客", "type": "VARCHAR",
                 "length": 64, "nullable": False},
                {"name": "content", "biz_name": "内容", "type": "TEXT",
                 "nullable": True},
            ],
            "indexes": [],
        }
    ],
}


def _mock_transport(payload: dict, expect_format: bool = True):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["temperature"] == 0
        assert body["messages"][0]["role"] == "system"
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler)


def _client(payload=None):
    cfg = LlmConfig(base_url="https://llm.test/v1", api_key="k", model="m")
    transport = _mock_transport(payload) if payload else httpx.MockTransport(
        lambda r: httpx.Response(200, json={}))
    return OpenAiCompatibleClient(cfg, transport=transport)


def test_unconfigured_client_raises():
    with pytest.raises(LlmNotConfiguredError):
        OpenAiCompatibleClient(LlmConfig(base_url="", api_key="", model=""))


def test_chat_json_parses_spec():
    payload = {
        "choices": [{"message": {"content": json.dumps(GUESTBOOK_LLM_JSON, ensure_ascii=False)}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20},
    }
    client = _client(payload)
    parsed, usage = client.chat_json("sys", "做个留言板")
    assert parsed["tables"][0]["name"] == "messages"
    assert usage.tokens_in == 10 and usage.tokens_out == 20


def test_generate_schema_validates_llm_output():
    payload = {"choices": [{"message": {"content": json.dumps(GUESTBOOK_LLM_JSON)}}],
               "usage": {}}
    schema, meta = generate_schema(_client(payload), "做个留言板收集访客反馈")
    assert isinstance(schema, SchemaSpec)
    assert schema.tables[0].name == "messages"
    assert meta["attempts"] == 1


def test_generate_schema_retries_then_succeeds():
    """第一次非法（无主键），第二次合法 → 回喂重试成功，attempts=2。"""
    bad = {
        "tables": [{
            "name": "t", "biz_name": "t",
            "columns": [{"name": "x", "type": "INTEGER"}],
        }]
    }
    good = GUESTBOOK_LLM_JSON
    responses = iter([
        {"choices": [{"message": {"content": json.dumps(bad)}}], "usage": {}},
        {"choices": [{"message": {"content": json.dumps(good)}}], "usage": {}},
    ])
    transport = httpx.MockTransport(
        lambda r: httpx.Response(200, json=next(responses))
    )
    client = OpenAiCompatibleClient(
        LlmConfig(base_url="https://llm.test/v1", api_key="k", model="m"),
        transport=transport,
    )
    schema, meta = generate_schema(client, "留言板")
    assert meta["attempts"] == 2
    assert schema.tables[0].pk_columns[0].name == "id"


def test_full_bare_chain_description_to_browse(tmp_path):
    """M0-5 端到端（API 层裸链路）：spec→DDL→落库→演示种子→CRUD 浏览。"""
    payload = {"choices": [{"message": {"content": json.dumps(GUESTBOOK_LLM_JSON)}}],
               "usage": {}}
    schema, _ = generate_schema(_client(payload), "做个留言板")

    # spec → 三方言之一（SQLite）DDL → 落库（含归属列）
    engine = make_engine("sqlite", str(tmp_path / "m05.db"))
    execute_ddl(engine, build_schema_ddl(schema, "sqlite"))

    # 演示种子（确定性，DR-08）
    seeds = generate_seed_rows(schema, rows_per_table=10)
    table_name = schema.tables[0].name
    assert len(seeds[table_name]) == 10

    with engine.begin() as conn:
        for row in seeds[table_name]:
            cols = ", ".join(row.keys())
            placeholders = ", ".join(f":{k}" for k in row)
            conn.execute(text(f"INSERT INTO {table_name} ({cols}) VALUES ({placeholders})"), row)

    # 浏览：反射 CRUD 引擎读回
    registry = CrudRegistry(engine, tables=[table_name])
    app = Flask(__name__)
    app.register_blueprint(create_crud_blueprint(registry, "/api/v1/demo"))
    client = app.test_client()

    resp = client.get(f"/api/v1/demo/tables/{table_name}/rows?page=1&page_size=50")
    body = resp.get_json()
    assert body["total"] == 10
    first = body["data"][0]
    assert first["created_by"] == 1  # 跨库身份对齐（users.id）
    assert first["created_at"]

    meta = client.get("/api/v1/demo/meta").get_json()
    meta_cols = {c["name"] for c in meta[table_name]["columns"]}
    assert {"id", "visitor", "content", "created_by", "created_at",
            "updated_by", "updated_at"} <= meta_cols


def test_seed_is_deterministic():
    payload = {"choices": [{"message": {"content": json.dumps(GUESTBOOK_LLM_JSON)}}],
               "usage": {}}
    schema, _ = generate_schema(_client(payload), "留言板")
    a = generate_seed_rows(schema, seed=7)
    b = generate_seed_rows(schema, seed=7)
    assert a == b  # 固定 seed 可复现
