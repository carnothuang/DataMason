"""自研数据层：多方言连接管理 + schema 反射（设计细化 §十一·一 数据层）。

M0-2 范围：
- engine 管理：SQLite/MySQL/PostgreSQL（SQLAlchemy create_engine，无外部工具运行时依赖）
- 连接测试
- schema 回读：inspect() 读表/列/主键/外键
- 建库：SQLite 建文件即建库；MySQL/PG 的 CREATE DATABASE 生成（M0 离线验证，真库 M1 补）
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

SUPPORTED_DIALECTS = ("sqlite", "mysql", "postgresql")


class DataAccessError(Exception):
    pass


@dataclass
class ColumnInfo:
    name: str
    type: str
    nullable: bool
    is_pk: bool = False

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.type,
            "nullable": self.nullable,
            "is_pk": self.is_pk,
        }


def make_engine(dialect: str, conn_uri: str) -> Engine:
    """按方言创建 engine。sqlite 支持文件路径与 :memory:。"""
    if dialect not in SUPPORTED_DIALECTS:
        raise DataAccessError(f"unsupported dialect: {dialect}")
    if dialect == "sqlite" and conn_uri not in (":memory:", "") and not conn_uri.startswith("sqlite:"):
        # 传入的是文件路径：确保父目录存在
        path = Path(conn_uri)
        path.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{path.as_posix()}"
    elif conn_uri.startswith(("sqlite://", "mysql", "postgresql")):
        url = conn_uri
    else:
        raise DataAccessError(f"无法识别的连接串: {conn_uri[:32]}...")
    return create_engine(url, future=True)


def check_connection(engine: Engine) -> bool:
    """连通性探测：SELECT 1。"""
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return True


def list_tables(engine: Engine) -> list[str]:
    return inspect(engine).get_table_names()


def read_columns(engine: Engine, table: str) -> list[ColumnInfo]:
    """回读真实 schema（以数据库为准）。"""
    insp = inspect(engine)
    pk_cols = set(insp.get_pk_constraint(table).get("constrained_columns") or [])
    result = []
    for col in insp.get_columns(table):
        result.append(
            ColumnInfo(
                name=col["name"],
                type=str(col["type"]),
                nullable=bool(col.get("nullable", True)),
                is_pk=col["name"] in pk_cols,
            )
        )
    return result


def execute_ddl(engine: Engine, statements: list[str]) -> None:
    """批量执行 DDL；SQLite 逐条提交。"""
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))


# ---- 建库（M0 离线生成；MySQL/PG 真库验证留 M1） ----

def create_database_ddl(dialect: str, db_name: str) -> str | None:
    """返回建库 DDL。SQLite 无 CREATE DATABASE（文件即库），返回 None。"""
    safe = _quote_ident(db_name)
    if dialect == "mysql":
        return f"CREATE DATABASE IF NOT EXISTS {safe} CHARACTER SET utf8mb4"
    if dialect == "postgresql":
        # PG 无 IF NOT EXISTS（< 14 语法），由调用方先查 pg_database
        return f"CREATE DATABASE {safe}"
    return None


def _quote_ident(name: str) -> str:
    """保守标识符白名单，防注入：仅允许字母数字下划线。"""
    if not name.replace("_", "").isalnum():
        raise DataAccessError(f"非法标识符: {name}")
    return f"`{name}`" if False else name  # MySQL 用反引号由调用方言决定时再扩展
