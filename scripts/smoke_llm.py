"""真实 LLM 联调（ark-code-latest）：一句话 → table_spec → DDL。

环境变量：DATAMASON_LLM_BASE_URL / DATAMASON_LLM_API_KEY / DATAMASON_LLM_MODEL
手动：python scripts/smoke_llm.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from server.ddl.ddl_builder import build_schema_ddl  # noqa: E402
from server.llm.client import LlmConfig, OpenAiCompatibleClient  # noqa: E402
from server.llm.structure import generate_schema  # noqa: E402


def main() -> int:
    cfg = LlmConfig(
        base_url=os.environ["DATAMASON_LLM_BASE_URL"],
        api_key=os.environ["DATAMASON_LLM_API_KEY"],
        model=os.environ.get("DATAMASON_LLM_MODEL", "ark-code-latest"),
    )
    client = OpenAiCompatibleClient(cfg)
    description = "做一个留言板收集访客反馈，记录访客名字、留言内容、时间和处理状态，后台可回复"
    print(f"描述：{description}\n")

    try:
        schema, meta = generate_schema(client, description)
    except Exception as exc:  # noqa: BLE001
        print(f"LLM_GENERATE_FAIL {type(exc).__name__}: {exc}")
        return 1

    print(f"尝试次数(attempts)：{meta['attempts']}  （1 = 一次通过）")
    print(f"biz_notes：{schema.biz_notes}")
    for t in schema.tables:
        fks = [f"{c.name}->{c.ref_table}.{c.ref_column}" for c in t.columns if c.is_fk]
        print(f"  表 {t.name}（{t.biz_name}）列={[c.name for c in t.columns]} 外键={fks}")

    ddl = build_schema_ddl(schema, "sqlite")
    print(f"\n生成 SQLite DDL {len(ddl)} 条，首条：\n{ddl[0]}\n")
    print("LLM_SMOKE_OK")
    # 落地一份供人工查看
    out = os.path.join(os.path.dirname(__file__), "..", ".workbuddy", "tmp", "llm_spec_sample.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(schema.model_dump(), f, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
