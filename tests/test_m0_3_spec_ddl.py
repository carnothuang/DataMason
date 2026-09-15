"""M0-3 验收：table_spec 校验 + 归属列注入 + 三方言 DDL + SQLite 端到端落库回读。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from server.dataaccess.manager import execute_ddl, list_tables, make_engine, read_columns
from server.ddl.ddl_builder import (
    OWNERSHIP_COLUMNS,
    DdlBuildError,
    build_schema_ddl,
    build_table_ddl,
    inject_ownership_columns,
)
from server.ddl.table_spec import ColumnSpec, SchemaSpec, TableSpecModel


def _guestbook_spec() -> SchemaSpec:
    """留言板：访客提交（public_form）+ 后台管理，对应走查/规划常用场景。"""
    return SchemaSpec(
        tables=[
            TableSpecModel(
                name="messages",
                biz_name="留言",
                columns=[
                    ColumnSpec(name="id", type="INTEGER", is_pk=True, autoincrement=True,
                               nullable=False),
                    ColumnSpec(name="visitor", biz_name="访客", type="VARCHAR", length=64,
                               nullable=False),
                    ColumnSpec(name="content", biz_name="内容", type="TEXT"),
                    ColumnSpec(name="status", biz_name="状态", type="VARCHAR", length=16,
                               default="open"),
                ],
                indexes=[["status"]],
            )
        ]
    )


def _inventory_spec() -> SchemaSpec:
    """进销存：orders → items 外键关联，验证拓扑排序与外键 DDL。"""
    return SchemaSpec(
        tables=[
            TableSpecModel(
                name="items",
                biz_name="商品",
                columns=[
                    ColumnSpec(name="id", type="INTEGER", is_pk=True, autoincrement=True,
                               nullable=False),
                    ColumnSpec(name="title", type="VARCHAR", length=64, nullable=False),
                ],
            ),
            TableSpecModel(
                name="orders",
                biz_name="订单",
                columns=[
                    ColumnSpec(name="id", type="INTEGER", is_pk=True, autoincrement=True,
                               nullable=False),
                    ColumnSpec(name="item_id", type="INTEGER", is_fk=True,
                               ref_table="items", ref_column="id", nullable=False),
                    ColumnSpec(name="qty", type="INTEGER", default="1"),
                ],
            ),
        ]
    )


# ---- 规格校验 ----

def test_valid_spec_passes():
    spec = _guestbook_spec()
    assert spec.table_map["messages"].pk_columns[0].name == "id"


def test_requires_single_pk():
    with pytest.raises(ValidationError):
        TableSpecModel(
            name="t",
            columns=[
                ColumnSpec(name="a", type="INTEGER", is_pk=True),
                ColumnSpec(name="b", type="INTEGER", is_pk=True),
            ],
        )


def test_fk_to_missing_table_rejected():
    with pytest.raises(ValidationError, match="不存在"):
        SchemaSpec(
            tables=[
                TableSpecModel(
                    name="orders",
                    columns=[
                        ColumnSpec(name="id", type="INTEGER", is_pk=True),
                        ColumnSpec(name="ghost_id", type="INTEGER", is_fk=True,
                                   ref_table="ghost", ref_column="id"),
                    ],
                )
            ]
        )


def test_fk_to_non_pk_column_rejected():
    with pytest.raises(ValidationError, match="主键"):
        SchemaSpec(
            tables=[
                TableSpecModel(
                    name="a",
                    columns=[
                        ColumnSpec(name="id", type="INTEGER", is_pk=True),
                        ColumnSpec(name="name", type="VARCHAR", length=32),
                    ],
                ),
                TableSpecModel(
                    name="b",
                    columns=[
                        ColumnSpec(name="id", type="INTEGER", is_pk=True),
                        ColumnSpec(name="a_name", type="VARCHAR", length=32, is_fk=True,
                                   ref_table="a", ref_column="name"),
                    ],
                ),
            ]
        )


def test_duplicate_table_rejected():
    t = TableSpecModel(
        name="t",
        columns=[ColumnSpec(name="id", type="INTEGER", is_pk=True)],
    )
    with pytest.raises(ValidationError, match="重名"):
        SchemaSpec(tables=[t, t.model_copy()])


# ---- 归属列注入（DR-05） ----

def test_ownership_columns_injected():
    table = inject_ownership_columns(_guestbook_spec().tables[0])
    names = {c.name for c in table.columns}
    assert set(OWNERSHIP_COLUMNS) <= names
    cb = next(c for c in table.columns if c.name == "created_by")
    assert cb.nullable is False and cb.type == "BIGINT"


def test_ownership_columns_not_duplicated():
    base = _guestbook_spec().tables[0]
    once = inject_ownership_columns(base)
    twice = inject_ownership_columns(once)
    assert sum(c.name == "created_by" for c in twice.columns) == 1


# ---- 三方言 DDL 生成 ----

def test_sqlite_ddl_contains_fk_and_ownership():
    ddl = build_table_ddl(_guestbook_spec().tables[0], "sqlite")
    assert "CREATE TABLE" in ddl
    assert "created_by BIGINT NOT NULL" in ddl
    assert "AUTOINCREMENT" in ddl


def test_mysql_ddl_uses_backtick_and_autoincrement():
    ddl = build_table_ddl(_guestbook_spec().tables[0], "mysql")
    assert "`messages`" in ddl
    assert "AUTO_INCREMENT" in ddl
    assert "TINYINT(1)" not in ddl  # 留言表无布尔列


def test_postgres_ddl_uses_identity():
    ddl = build_table_ddl(_guestbook_spec().tables[0], "postgresql")
    assert '"messages"' in ddl
    assert "GENERATED ALWAYS AS IDENTITY" in ddl
    assert "TIMESTAMP" in ddl


def test_unsupported_dialect_raises():
    with pytest.raises(DdlBuildError):
        build_table_ddl(_guestbook_spec().tables[0], "oracle")


def test_topo_sort_puts_referenced_first():
    statements = build_schema_ddl(_inventory_spec(), "sqlite")
    create_items = next(s for s in statements if s.startswith("CREATE TABLE") and "items" in s)
    create_orders = next(s for s in statements if s.startswith("CREATE TABLE") and "orders" in s)
    assert statements.index(create_items) < statements.index(create_orders)
    assert "REFERENCES items(id)" in create_orders


# ---- 端到端：spec → DDL → SQLite 落库 → schema 回读 ----

def test_spec_to_ddl_to_sqlite_roundtrip(tmp_path):
    spec = _inventory_spec()
    statements = build_schema_ddl(spec, "sqlite")
    engine = make_engine("sqlite", str(tmp_path / "inv.db"))
    execute_ddl(engine, statements)

    tables = set(list_tables(engine))
    assert {"items", "orders"} <= tables

    orders_cols = {c.name: c for c in read_columns(engine, "orders")}
    for col in ("item_id", "qty", "created_by", "created_at", "updated_by", "updated_at"):
        assert col in orders_cols

    # 归属列可写入 users.id（跨库身份对齐：这里用裸 bigint 验证物理前提）
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(text("INSERT INTO items (title, created_by, created_at) VALUES ('螺丝', 1, CURRENT_TIMESTAMP)"))
        conn.execute(text("INSERT INTO orders (item_id, qty, created_by, created_at) VALUES (1, 100, 1, CURRENT_TIMESTAMP)"))
        row = conn.execute(text("SELECT item_id, qty, created_by FROM orders")).fetchone()
    assert tuple(row) == (1, 100, 1)
