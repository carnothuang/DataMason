"""M6-0 地基回归：骨架模型 / 表级生成 / 字段级生成 / 合并器（0.12.0 / DR-35）。

离线验收线（版本规划 §六）：骨架强校验、relations→外键列确定性映射、
字段级两模式与外键约束、合并后过 SchemaSpec 全校验并给出可读错误定位。
"""
from __future__ import annotations

import json

import httpx
import pytest
from pydantic import ValidationError

from server.ddl.skeleton import (
    SchemaSkeleton,
    SkeletonMergeError,
    SkeletonTable,
    map_relations_to_columns,
    merge_skeleton_columns,
)
from server.llm.client import LlmCallError, LlmConfig, OpenAiCompatibleClient
from server.llm.structure import generate_skeleton, generate_table_columns

SKELETON = {
    "biz_notes": "客户与订单",
    "tables": [
        {"name": "customers", "biz_name": "客户", "biz_notes": "客户档案"},
        {
            "name": "orders", "biz_name": "订单", "biz_notes": "客户下的订单",
            "relations": [{"to_table": "customers", "note": "订单属于客户"}],
        },
    ],
}


def _client_for(contents: list):
    it = iter(contents)

    def handler(request: httpx.Request) -> httpx.Response:
        item = next(it)
        content = (json.dumps(item, ensure_ascii=False)
                   if isinstance(item, (dict, list)) else item)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": content}}], "usage": {},
        })

    cfg = LlmConfig(base_url="https://llm.test/v1", api_key="k", model="m")
    return OpenAiCompatibleClient(cfg, transport=httpx.MockTransport(handler))


# ---------------- M6-0-1 骨架模型：强校验 ----------------

def test_skeleton_model_accepts_legal():
    sk = SchemaSkeleton.model_validate(SKELETON)
    assert sk.table_names == ["customers", "orders"]
    assert sk.table_map["orders"].relations[0].to_table == "customers"


def test_skeleton_rejects_duplicate_table():
    data = {"tables": [{"name": "t"}, {"name": "t"}]}
    with pytest.raises(ValidationError, match="重名表"):
        SchemaSkeleton.model_validate(data)


def test_skeleton_rejects_illegal_name():
    with pytest.raises(ValidationError):
        SkeletonTable.model_validate({"name": "1bad-name"})


def test_skeleton_rejects_dangling_relation():
    data = {"tables": [{"name": "orders", "relations": [{"to_table": "ghost"}]}]}
    with pytest.raises(ValidationError, match="不存在"):
        SchemaSkeleton.model_validate(data)


def test_skeleton_rejects_self_loop():
    data = {"tables": [{"name": "orders", "relations": [{"to_table": "orders"}]}]}
    with pytest.raises(ValidationError, match="自环"):
        SchemaSkeleton.model_validate(data)


def test_skeleton_rejects_empty_tables():
    with pytest.raises(ValidationError):
        SchemaSkeleton.model_validate({"tables": []})


# ---------------- M6-0-2 表级生成 + 确定性映射 ----------------

def test_map_relations_to_columns_deterministic():
    sk = SchemaSkeleton.model_validate(SKELETON)
    cols = map_relations_to_columns(sk)
    assert [c.name for c in cols["customers"]] == ["id"]
    assert [c.name for c in cols["orders"]] == ["id", "customers_id"]
    fk = cols["orders"][1]
    assert fk.is_fk and fk.ref_table == "customers" and fk.ref_column == "id"
    assert cols["orders"][0].is_pk and cols["orders"][0].autoincrement


def test_map_relations_uses_semantic_suffix_on_conflict():
    data = {"tables": [
        {"name": "customers"},
        {"name": "orders", "relations": [
            {"to_table": "customers"}, {"to_table": "customers"}]},
    ]}
    cols = map_relations_to_columns(SchemaSkeleton.model_validate(data))
    names = [c.name for c in cols["orders"]]
    assert names == ["id", "customers_id", "related_customers_id"]
    assert len(names) == len(set(names))


def test_generate_skeleton_parses_llm_output():
    sk, meta = generate_skeleton(_client_for([SKELETON]), "客户与订单")
    assert isinstance(sk, SchemaSkeleton)
    assert sk.table_names == ["customers", "orders"]
    assert meta["attempts"] == 1


def test_generate_skeleton_normalizes_string_relations():
    data = {"tables": [
        {"name": "customers"},
        {"name": "orders", "relations": ["customers"]},
    ]}
    sk, _ = generate_skeleton(_client_for([data]), "客户与订单")
    assert sk.table_map["orders"].relations[0].to_table == "customers"


def test_generate_skeleton_retries_then_succeeds():
    bad = {"tables": [{"name": "orders", "relations": [{"to_table": "ghost"}]}]}
    sk, meta = generate_skeleton(_client_for([bad, SKELETON]), "客户与订单")
    assert meta["attempts"] == 2
    assert sk.table_names == ["customers", "orders"]


def test_generate_skeleton_fails_after_max_retry():
    bad = {"tables": [{"name": "orders", "relations": [{"to_table": "ghost"}]}]}
    with pytest.raises(LlmCallError):
        generate_skeleton(_client_for([bad, bad, bad]), "客户与订单")


# ---------------- M6-0-3 字段级生成 ----------------

def _sk() -> SchemaSkeleton:
    return SchemaSkeleton.model_validate(SKELETON)


def _order_business() -> list[dict]:
    return [
        {"name": "amount", "biz_name": "金额", "type": "DECIMAL", "nullable": False},
        {"name": "status", "biz_name": "状态", "type": "VARCHAR", "length": 32},
    ]


def test_generate_table_columns_overwrite_builds_full_table():
    client = _client_for([{"columns": _order_business()}])
    cols, meta = generate_table_columns(client, _sk(), "orders", "订单要有金额和状态")
    names = [c.name for c in cols]
    assert names[:2] == ["id", "customers_id"]
    assert "amount" in names and "status" in names
    assert {"created_by", "created_at", "updated_by", "updated_at"} <= set(names)
    assert next(c for c in cols if c.name == "id").is_pk
    assert next(c for c in cols if c.name == "customers_id").is_fk
    assert meta["attempts"] == 1


def test_generate_table_columns_append_renames_conflict():
    existing = [
        {"name": "id", "type": "INTEGER", "is_pk": True, "autoincrement": True,
         "nullable": False},
        {"name": "customers_id", "type": "INTEGER", "is_fk": True,
         "ref_table": "customers", "ref_column": "id"},
        {"name": "amount", "type": "DECIMAL"},
    ]
    new = [
        {"name": "remark", "biz_name": "备注", "type": "TEXT"},
        {"name": "amount", "type": "DECIMAL"},  # 与已有字段冲突 → 确定性改名
    ]
    cols, _ = generate_table_columns(
        _client_for([{"columns": new}]), _sk(), "orders", "再加备注",
        mode="append", existing_columns=existing)
    names = [c.name for c in cols]
    assert names.count("amount") == 1
    assert "amount_2" in names and "remark" in names


def test_generate_table_columns_rejects_dangling_fk():
    bad = {"columns": [
        {"name": "amount", "type": "DECIMAL"},
        {"name": "ghost_id", "type": "INTEGER", "is_fk": True,
         "ref_table": "ghost", "ref_column": "id"},
    ]}
    with pytest.raises(LlmCallError):
        generate_table_columns(_client_for([bad, bad, bad]), _sk(), "orders", "x")


def test_generate_table_columns_retries_on_dangling_fk_then_succeeds():
    bad = {"columns": [
        {"name": "ghost_id", "type": "INTEGER", "is_fk": True,
         "ref_table": "ghost", "ref_column": "id"},
    ]}
    good = {"columns": _order_business()}
    cols, meta = generate_table_columns(
        _client_for([bad, good]), _sk(), "orders", "x")
    assert meta["attempts"] == 2
    assert "amount" in [c.name for c in cols]


def test_generate_table_columns_rejects_self_fk():
    bad = {"columns": [
        {"name": "parent_id", "type": "INTEGER", "is_fk": True,
         "ref_table": "orders", "ref_column": "id"},
    ]}
    with pytest.raises(LlmCallError):
        generate_table_columns(_client_for([bad, bad, bad]), _sk(), "orders", "x")


def test_generate_table_columns_rejects_non_pk_ref_column():
    bad = {"columns": [
        {"name": "c_id", "type": "INTEGER", "is_fk": True,
         "ref_table": "customers", "ref_column": "name"},
    ]}
    with pytest.raises(LlmCallError):
        generate_table_columns(_client_for([bad, bad, bad]), _sk(), "orders", "x")


def test_generate_table_columns_rejects_llm_pk_and_reserved():
    with_pk = {"columns": [{"name": "id2", "type": "INTEGER", "is_pk": True}]}
    with pytest.raises(LlmCallError):
        generate_table_columns(_client_for([with_pk] * 3), _sk(), "orders", "x")
    reserved = {"columns": [{"name": "created_by", "type": "BIGINT"}]}
    with pytest.raises(LlmCallError):
        generate_table_columns(_client_for([reserved] * 3), _sk(), "orders", "x")


# ---------------- M6-0-4 合并器 ----------------

def _columns_map() -> dict:
    return {
        "customers": [
            {"name": "name", "biz_name": "姓名", "type": "VARCHAR", "length": 64,
             "nullable": False},
        ],
        "orders": [
            {"name": "customers_id", "type": "INTEGER", "is_fk": True,
             "ref_table": "customers", "ref_column": "id"},
            {"name": "amount", "type": "DECIMAL"},
        ],
    }


def test_merge_skeleton_columns_builds_full_spec():
    spec = merge_skeleton_columns(_sk(), _columns_map())
    customers = spec.table_map["customers"]
    orders = spec.table_map["orders"]
    assert customers.pk_columns[0].name == "id"
    assert {"created_by", "created_at", "updated_by", "updated_at"} <= customers.column_names
    assert "customers_id" in orders.column_names
    assert spec.biz_notes == "客户与订单"


def test_merge_errors_when_table_unguided():
    with pytest.raises(SkeletonMergeError) as ei:
        merge_skeleton_columns(_sk(), {"customers": [
            {"name": "name", "type": "VARCHAR", "length": 64}]})
    errors = ei.value.errors
    assert any(e["table"] == "orders" and "还没有字段" in e["message"] for e in errors)
    assert all({"table", "column", "message"} <= set(e) for e in errors)


def test_merge_errors_when_relation_lacks_fk_column():
    with pytest.raises(SkeletonMergeError) as ei:
        merge_skeleton_columns(_sk(), {
            "customers": [{"name": "name", "type": "VARCHAR", "length": 64}],
            "orders": [{"name": "amount", "type": "DECIMAL"}],
        })
    assert any(e["table"] == "orders" and "关系" in e["message"] for e in ei.value.errors)


def test_merge_errors_locate_dangling_fk():
    with pytest.raises(SkeletonMergeError) as ei:
        merge_skeleton_columns(_sk(), {
            "customers": [{"name": "name", "type": "VARCHAR", "length": 64}],
            "orders": [
                {"name": "customers_id", "type": "INTEGER", "is_fk": True,
                 "ref_table": "customers", "ref_column": "id"},
                {"name": "ghost_id", "type": "INTEGER", "is_fk": True,
                 "ref_table": "ghost", "ref_column": "id"},
            ],
        })
    errors = ei.value.errors
    assert any(e["table"] == "orders" and "ghost" in e["message"] for e in errors)


def test_merge_injects_id_when_missing():
    columns = {
        "customers": [{"name": "name", "type": "VARCHAR", "length": 64}],
        "orders": [{"name": "customers_id", "type": "INTEGER", "is_fk": True,
                    "ref_table": "customers", "ref_column": "id"}],
    }
    spec = merge_skeleton_columns(_sk(), columns)
    assert spec.table_map["orders"].pk_columns[0].name == "id"
