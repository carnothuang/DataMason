"""MySQL 真库冒烟：建库→建表(归属列+外键)→回读→CRUD→清理。

连接信息从环境变量读，默认本机 root：
  DATAMASON_TEST_MYSQL_URL=mysql+pymysql://root:123456@127.0.0.1:3306
手动运行：python scripts/smoke_mysql.py
"""
from __future__ import annotations

import os
import sys
import time
import uuid

import pymysql
from sqlalchemy import create_engine, inspect, text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from datamason.dataaccess.manager import read_columns  # noqa: E402
from datamason.ddl.ddl_builder import build_schema_ddl  # noqa: E402
from datamason.ddl.table_spec import ColumnSpec, SchemaSpec, TableSpecModel  # noqa: E402

ADMIN_URL = os.environ.get(
    "DATAMASON_TEST_MYSQL_URL", "mysql+pymysql://root:123456@127.0.0.1:3306"
)


def main() -> int:
    db_name = f"dm_smoke_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    admin_engine = create_engine(ADMIN_URL, future=True)

    # ① 建库
    with admin_engine.begin() as conn:
        conn.execute(text(
            f"CREATE DATABASE {db_name} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        ))
    print(f"[1] database created: {db_name}")

    db_engine = create_engine(f"{ADMIN_URL.rstrip('/')}/{db_name}", future=True)
    try:
        # ② spec → MySQL DDL（含归属列注入、外键、自增）→ 落库
        spec = SchemaSpec(tables=[
            TableSpecModel(
                name="items",
                biz_name="商品",
                columns=[
                    ColumnSpec(name="id", type="INTEGER", is_pk=True,
                               autoincrement=True, nullable=False),
                    ColumnSpec(name="title", type="VARCHAR", length=64, nullable=False),
                ],
            ),
            TableSpecModel(
                name="orders",
                biz_name="订单",
                columns=[
                    ColumnSpec(name="id", type="INTEGER", is_pk=True,
                               autoincrement=True, nullable=False),
                    ColumnSpec(name="item_id", type="INTEGER", is_fk=True,
                               ref_table="items", ref_column="id", nullable=False),
                    ColumnSpec(name="qty", type="INTEGER", default="1"),
                ],
            ),
        ])
        stmts = build_schema_ddl(spec, "mysql")
        with db_engine.begin() as conn:
            for stmt in stmts:
                conn.execute(text(stmt))
        print(f"[2] {len(stmts)} DDL statements executed (tables+ownership+FK)")

        # ③ 回读 schema（以数据库为准）
        tables = set(inspect(db_engine).get_table_names())
        assert {"items", "orders"} <= tables, tables
        orders_cols = {c.name: c for c in read_columns(db_engine, "orders")}
        for required in ("item_id", "qty", "created_by", "created_at",
                         "updated_by", "updated_at"):
            assert required in orders_cols, f"missing {required}"
        fks = inspect(db_engine).get_foreign_keys("orders")
        assert fks and fks[0]["referred_table"] == "items", fks
        print("[3] schema readback OK; FK:", fks[0]["name"], "-> items")

        # ④ CRUD（含归属列）
        with db_engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO items (title, created_by, created_at) "
                "VALUES ('螺丝', 1, NOW())"
            ))
            conn.execute(text(
                "INSERT INTO orders (item_id, qty, created_by, created_at) "
                "VALUES (1, 100, 1, NOW())"
            ))
            row = conn.execute(text(
                "SELECT o.item_id, o.qty, o.created_by, i.title "
                "FROM orders o JOIN items i ON i.id = o.item_id"
            )).fetchone()
        assert tuple(row) == (1, 100, 1, "螺丝"), row
        print("[4] CRUD + JOIN OK:", tuple(row))

        print("MYSQL_SMOKE_OK")
        return 0
    finally:
        # ⑤ 清理
        with admin_engine.begin() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {db_name}"))
        print(f"[5] cleaned: {db_name}")


if __name__ == "__main__":
    sys.exit(main())
