"""M0-2 验收：数据层三方言「建库→建表→回读」。

SQLite 走真实文件库；MySQL/PG 在无实例环境下以「方言 DDL 生成 + 标识符防护」
离线单测覆盖（已知限制，M1 前补真库冒烟）。
"""
from __future__ import annotations

import pytest
from sqlalchemy import inspect

from datamason.dataaccess.manager import (
    DataAccessError,
    check_connection,
    create_database_ddl,
    execute_ddl,
    list_tables,
    make_engine,
    read_columns,
)


def test_sqlite_create_table_and_readback(tmp_path):
    """SQLite 真实冒烟：建文件库→建表→回读列/主键。"""
    db_file = tmp_path / "biz" / "guestbook.db"
    engine = make_engine("sqlite", str(db_file))

    assert check_connection(engine) is True

    execute_ddl(
        engine,
        [
            """CREATE TABLE guests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name VARCHAR(64) NOT NULL,
                content TEXT,
                created_at DATETIME
            )""",
        ],
    )

    tables = list_tables(engine)
    assert "guests" in tables

    cols = {c.name: c for c in read_columns(engine, "guests")}
    assert set(cols) >= {"id", "name", "content", "created_at"}
    assert cols["id"].is_pk is True
    assert cols["name"].nullable is False
    assert "VARCHAR" in cols["name"].type

    engine.dispose()
    assert db_file.exists()


def test_sqlite_foreign_key_roundtrip(tmp_path):
    """带外键的多表建表 + 回读外键（外键规范物理验证）。"""
    engine = make_engine("sqlite", str(tmp_path / "fk.db"))
    execute_ddl(
        engine,
        [
            "CREATE TABLE orders (id INTEGER PRIMARY KEY, note VARCHAR(128))",
            """CREATE TABLE items (
                id INTEGER PRIMARY KEY,
                order_id INTEGER NOT NULL REFERENCES orders(id),
                title VARCHAR(64) NOT NULL
            )""",
        ],
    )
    fks = inspect(engine).get_foreign_keys("items")
    assert len(fks) == 1
    assert fks[0]["referred_table"] == "orders"
    assert fks[0]["constrained_columns"] == ["order_id"]


def test_unsupported_dialect_rejected():
    with pytest.raises(DataAccessError):
        make_engine("oracle", "x")


def test_ddl_builder_per_dialect():
    """MySQL/PG 建库 DDL 离线验证（本机无实例时的覆盖手段）。"""
    assert create_database_ddl("sqlite", "dm_1") is None
    mysql_ddl = create_database_ddl("mysql", "dm_demo")
    assert "CREATE DATABASE IF NOT EXISTS" in mysql_ddl
    assert "utf8mb4" in mysql_ddl
    pg_ddl = create_database_ddl("postgresql", "dm_demo")
    assert pg_ddl == "CREATE DATABASE dm_demo"


def test_identifier_injection_rejected():
    with pytest.raises(DataAccessError):
        create_database_ddl("mysql", "dm; DROP DATABASE mysql;")
