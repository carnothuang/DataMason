"""W1 网关改进回归：_loads_llm_json 容错解析 + llm_timeout 配置。

0.12.0 两段式（骨架/字段）地基：真机模型常输出 ```json 围栏 / 解释前缀 /
尾随逗号，严取 json.loads 直接失败；此处固化容错行为。
"""
from __future__ import annotations

import json

import httpx
import pytest

from server.config import Config, get_config
from server.llm.client import LlmConfig, OpenAiCompatibleClient, _loads_llm_json
from server.llm.structure import generate_schema

PAYLOAD = {"tables": [{"name": "t", "biz_name": "表", "columns": [
    {"name": "id", "type": "INTEGER", "is_pk": True, "autoincrement": True},
]}], "biz_notes": "样例"}


def _client(content: str):
    payload = {"choices": [{"message": {"content": content}}], "usage": {}}
    cfg = LlmConfig(base_url="https://llm.test/v1", api_key="k", model="m")
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
    return OpenAiCompatibleClient(cfg, transport=transport)


def test_loads_plain_json():
    assert _loads_llm_json('{"a": 1}') == {"a": 1}


def test_loads_markdown_fence():
    text = '```json\n{"a": 1}\n```'
    assert _loads_llm_json(text) == {"a": 1}


def test_loads_prefix_suffix_text():
    text = '好的，这是结果：\n{"tables": [{"name": "t"}]}\n以上。'
    assert _loads_llm_json(text)["tables"][0]["name"] == "t"


def test_loads_trailing_commas():
    assert _loads_llm_json('{"a": 1, "b": [1, 2,],}') == {"a": 1, "b": [1, 2]}


def test_loads_keeps_string_content_intact():
    text = '{"a": "含逗号,和}大括号,的字符串", "b": [{"x": "}，", "y": 1}]}'
    assert _loads_llm_json(text)["a"] == "含逗号,和}大括号,的字符串"


def test_loads_no_json_raises():
    with pytest.raises(ValueError):
        _loads_llm_json("这是一个说明，没有任何 JSON")


def test_loads_non_text_raises():
    with pytest.raises(TypeError):
        _loads_llm_json(12345)


def test_chat_json_tolerates_fenced_output():
    content = f"```json\n{json.dumps(PAYLOAD, ensure_ascii=False)}\n```"
    parsed, usage = _client(content).chat_json("sys", "业务描述：样例")
    assert parsed["tables"][0]["name"] == "t"


def test_generate_schema_tolerates_fenced_output():
    content = f"好的：\n```\n{json.dumps(PAYLOAD, ensure_ascii=False)}\n```"
    schema, meta = generate_schema(_client(content), "业务描述：样例")
    assert schema.tables[0].name == "t"
    assert meta["attempts"] == 1


def test_config_llm_timeout_default():
    cfg = Config()
    assert cfg.llm_timeout == 300.0
    assert cfg.as_flask_config()["DATAMASON_LLM_TIMEOUT"] == 300.0


def test_get_config_llm_timeout_env(monkeypatch):
    monkeypatch.setenv("DATAMASON_LLM_TIMEOUT", "150")
    cfg = get_config()
    assert cfg.llm_timeout == 150.0


def test_get_config_llm_timeout_invalid_env_defaults(monkeypatch):
    monkeypatch.setenv("DATAMASON_LLM_TIMEOUT", "abc")
    cfg = get_config()
    assert cfg.llm_timeout == 300.0


def test_settings_service_timeout_propagates(app):
    from server.settings.service import get_default_llm_client

    app.config["DATAMASON_LLM_TIMEOUT"] = 120
    client = get_default_llm_client(app)
    if client is None:
        pytest.skip("当前环境无默认 LLM 配置")
    assert client.config.timeout == 120