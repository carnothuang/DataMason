"""M8-0 地基：CREATE TABLE → SchemaSpec 解析器（0.14.0 / DR-42）。

离线验收线（版本规划 §六）：三方言典型 DDL 归一为平台八类列；非建表语句、
不支持类型、复合主键、重名列、悬空/非主键外键分别拒绝并给出表/列定位；
无主键表由系统补 id；解析结果过 SchemaSpec 全校验。
"""
from __future__ import annotations

import pytest

from datamason.ddl.sql_import import SqlImportError, parse_create_tables

SQLITE_SQL = """
CREATE TABLE customer (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name VARCHAR(100) NOT NULL,
  age INTEGER,
  balance DECIMAL(10,2)
);
CREATE TABLE "order" (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  customer_id INTEGER NOT NULL REFERENCES customer(id),
  note TEXT,
  UNIQUE(customer_id)
);
"""

MYSQL_SQL = """
CREATE TABLE customer (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(100) NOT NULL,
  active TINYINT(1)
);
CREATE TABLE orders (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  customer_id BIGINT NOT NULL,
  CONSTRAINT fk_cust FOREIGN KEY (customer_id) REFERENCES customer(id)
);
"""

PG_SQL = """
CREATE TABLE customer (
  id SERIAL PRIMARY KEY,
  name VARCHAR(100) NOT NULL,
  active BOOLEAN,
  balance NUMERIC(12,2)
);
CREATE TABLE orders (
  id BIGSERIAL PRIMARY KEY,
  customer_id INTEGER REFERENCES customer(id)
);
"""


def test_sqlite_parse():
    spec = parse_create_tables(SQLITE_SQL, "sqlite")
    assert len(spec.tables) == 2
    customer, order = spec.tables
    assert customer.name == "customer"
    assert order.name == "order"
    pk = customer.pk_columns[0]
    assert pk.name == "id" and pk.autoincrement and pk.type == "INTEGER"
    name = next(c for c in customer.columns if c.name == "name")
    assert name.type == "VARCHAR" and name.length == 100 and not name.nullable
    fk = next(c for c in order.columns if c.name == "customer_id")
    assert fk.is_fk and fk.ref_table == "customer" and fk.ref_column == "id"
    assert fk.unique


def test_mysql_parse():
    spec = parse_create_tables(MYSQL_SQL, "mysql")
    customer, orders = spec.tables
    pk = customer.pk_columns[0]
    assert pk.type == "BIGINT" and pk.autoincrement
    active = next(c for c in customer.columns if c.name == "active")
    assert active.type == "BOOLEAN"
    fk = next(c for c in orders.columns if c.name == "customer_id")
    assert fk.is_fk and fk.ref_table == "customer"


def test_postgres_parse():
    spec = parse_create_tables(PG_SQL, "postgresql")
    customer, orders = spec.tables
    assert customer.pk_columns[0].type == "INTEGER"
    assert orders.pk_columns[0].type == "BIGINT"
    balance = next(c for c in customer.columns if c.name == "balance")
    assert balance.type == "DECIMAL"
    fk = next(c for c in orders.columns if c.name == "customer_id")
    assert fk.is_fk and fk.ref_table == "customer"


def test_no_pk_gets_id():
    spec = parse_create_tables(
        "CREATE TABLE thing (name VARCHAR(20));", "sqlite"
    )
    table = spec.tables[0]
    pk = table.pk_columns[0]
    assert pk.name == "id" and pk.autoincrement and pk.type == "INTEGER"
    assert len(table.columns) == 2


def test_reject_non_create_statement():
    with pytest.raises(SqlImportError) as ei:
        parse_create_tables(
            "CREATE TABLE a (id INTEGER PRIMARY KEY); INSERT INTO a VALUES (1);",
            "sqlite",
        )
    assert any("CREATE TABLE" in e["message"] for e in ei.value.errors)


def test_reject_unsupported_type():
    with pytest.raises(SqlImportError) as ei:
        parse_create_tables(
            "CREATE TABLE a (id INTEGER PRIMARY KEY, data JSON);", "mysql"
        )
    err = ei.value.errors[0]
    assert err["column"] == "data" and "JSON" in err["message"]


def test_reject_composite_pk():
    with pytest.raises(SqlImportError) as ei:
        parse_create_tables(
            "CREATE TABLE a (x INTEGER, y INTEGER, PRIMARY KEY (x, y));",
            "sqlite",
        )
    assert any("复合主键" in e["message"] for e in ei.value.errors)


def test_reject_duplicate_column():
    with pytest.raises(SqlImportError) as ei:
        parse_create_tables(
            "CREATE TABLE a (id INTEGER PRIMARY KEY, id INTEGER);", "sqlite"
        )
    assert ei.value.errors


def test_reject_dangling_fk():
    with pytest.raises(SqlImportError) as ei:
        parse_create_tables(
            "CREATE TABLE a (id INTEGER PRIMARY KEY, b_id INTEGER REFERENCES b(id));",
            "sqlite",
        )
    assert any("不存在的表" in e["message"] for e in ei.value.errors)


def test_reject_fk_to_non_pk():
    sql = """
    CREATE TABLE a (id INTEGER PRIMARY KEY, name VARCHAR(10));
    CREATE TABLE b (id INTEGER PRIMARY KEY, a_name VARCHAR(10) REFERENCES a(name));
    """
    with pytest.raises(SqlImportError) as ei:
        parse_create_tables(sql, "sqlite")
    assert any("引用主键" in e["message"] for e in ei.value.errors)


def test_reject_duplicate_table():
    with pytest.raises(SqlImportError) as ei:
        parse_create_tables(
            "CREATE TABLE a (id INTEGER PRIMARY KEY);"
            "CREATE TABLE a (id INTEGER PRIMARY KEY);",
            "sqlite",
        )
    assert any("重名表" in e["message"] for e in ei.value.errors)


def test_reject_ownership_column():
    with pytest.raises(SqlImportError) as ei:
        parse_create_tables(
            "CREATE TABLE a (id INTEGER PRIMARY KEY, created_by INTEGER);",
            "sqlite",
        )
    assert ei.value.errors


def test_reject_bad_dialect():
    with pytest.raises(SqlImportError):
        parse_create_tables("SELECT 1;", "oracle")
