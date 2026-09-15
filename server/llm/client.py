"""OpenAI 兼容 LLM 网关（设计细化 §十一·〇/二）。

纪律（移植 superset-lite 网关设计，零运行时依赖、自行实现）：
- temperature=0（确定性优先）
- JSON 输出契约：要求模型直接返回 table_spec JSON
- 自检重试在上层 structure.py；本层只负责通信与用量埋点
- 配置来源：环境变量/设置页（DATAMASON_LLM_BASE_URL / API_KEY / MODEL）
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import httpx


class LlmNotConfiguredError(RuntimeError):
    pass


class LlmCallError(RuntimeError):
    pass


@dataclass
class LlmConfig:
    base_url: str
    api_key: str
    model: str
    temperature: float = 0.0
    timeout: float = 60.0


@dataclass
class Usage:
    tokens_in: int = 0
    tokens_out: int = 0
    duration_ms: int = 0


class OpenAiCompatibleClient:
    """最小 OpenAI Chat Completions 客户端（/chat/completions）。"""

    def __init__(self, config: LlmConfig, transport: httpx.BaseTransport | None = None):
        # transport 参数仅供测试注入（httpx MockTransport）
        if not config.base_url or not config.model:
            raise LlmNotConfiguredError("LLM 未配置：请在首次启动向导/设置页填写端点与模型")
        self.config = config
        self._http = httpx.Client(
            base_url=config.base_url.rstrip("/"),
            timeout=config.timeout,
            transport=transport,
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
            },
        )

    def chat_json(self, system_prompt: str, user_prompt: str) -> tuple[object, Usage]:
        """要求模型返回单个 JSON 文档；解析为 Python 对象。"""
        import time

        payload = {
            "model": self.config.model,
            "temperature": self.config.temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        started = time.monotonic()
        try:
            resp = self._http.post("/chat/completions", json=payload)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise LlmCallError(f"LLM 请求失败: {exc}") from exc
        duration_ms = int((time.monotonic() - started) * 1000)

        try:
            body = resp.json()
            content = body["choices"][0]["message"]["content"]
            parsed = json.loads(content)
        except (KeyError, IndexError, json.JSONDecodeError) as exc:
            raise LlmCallError(f"LLM 返回不是合法 JSON: {exc}") from exc

        usage = body.get("usage", {})
        return parsed, Usage(
            tokens_in=usage.get("prompt_tokens", 0),
            tokens_out=usage.get("completion_tokens", 0),
            duration_ms=duration_ms,
        )

    def close(self):
        self._http.close()
