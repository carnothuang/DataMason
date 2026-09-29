"""审计归属列归一化：真实模型常自带 created_at/created_by，解析前静默剥离。"""
from __future__ import annotations

from datamason.llm.structure import _normalize_ownership_columns
from datamason.ddl.table_spec import SchemaSpec


def test_normalize_strips_pure_audit_columns():
    raw = {
        "tables": [
            {
                "name": "orders",
                "columns": [
                    {"name": "id", "type": "INTEGER", "is_pk": True},
                    {"name": "title", "type": "VARCHAR", "length": 32},
                    {"name": "created_at", "type": "DATETIME"},
                    {"name": "updated_at", "type": "DATETIME"},
                    {"name": "created_by", "type": "BIGINT"},
                ],
            }
        ]
    }
    norm = _normalize_ownership_columns(raw)
    names = [c["name"] for c in norm["tables"][0]["columns"]]
    assert names == ["id", "title"]
    # 归一化后可通过校验（系统注入归属列接管）
    schema = SchemaSpec.model_validate(norm)
    assert len(schema.tables[0].columns) == 2


def test_normalize_keeps_actor_fk_for_retry_rejection():
    """created_by 被当业务外键（经办人）时保留，交由强校验拦截重试。"""
    raw = {
        "tables": [
            {
                "name": "messages",
                "columns": [
                    {"name": "id", "type": "INTEGER", "is_pk": True},
                    {"name": "created_by", "type": "BIGINT", "is_fk": True,
                     "ref_table": "visitors", "ref_column": "id"},
                ],
            },
            {
                "name": "visitors",
                "columns": [
                    {"name": "id", "type": "INTEGER", "is_pk": True},
                ],
            },
        ]
    }
    import pytest
    from pydantic import ValidationError

    norm = _normalize_ownership_columns(raw)
    msg_names = [c["name"] for c in norm["tables"][0]["columns"]]
    assert "created_by" in msg_names  # 外键保留
    with pytest.raises(ValidationError, match="系统保留"):
        SchemaSpec.model_validate(norm)
