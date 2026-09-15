"""元数据模型（元数据库，全部惰性建表）——对齐《设计细化》§十一·八。

表清单与设计细化 §十一·八 一致：users / connections / deployments /
table_specs / page_specs / crud_configs / deployment_menus /
deployment_menu_grants / deployment_members / flow_specs /
activity_logs / ai_generations / llm_models。
关联闭环（防断档，见 §八 末尾闭环说明）：
- deployment_menus.ref_id → page_specs.id
- flow_specs.steps[].page_id → page_specs.id
- 业务表 created_by/updated_by → users.id（跨库身份靠全局 users.id 对齐）
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


class JsonText:
    """列类型辅助：JSON 以 TEXT 存储，Python 侧 dict/list 序列化。"""


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(64), default="")
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Connection(Base):
    __tablename__ = "connections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    dialect: Mapped[str] = mapped_column(String(16), nullable=False)  # sqlite/mysql/postgresql
    conn_uri: Mapped[str] = mapped_column(Text, nullable=False)  # 加密存储（M0 明文占位，M1 换 cryptography）
    status: Mapped[str] = mapped_column(String(16), default="ok")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Deployment(Base):
    __tablename__ = "deployments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    biz_name: Mapped[str] = mapped_column(String(128), nullable=False)
    purpose: Mapped[str] = mapped_column(String(512), default="")
    data_mode: Mapped[str] = mapped_column(String(8), default="create")  # create/link
    connection_id: Mapped[int | None] = mapped_column(ForeignKey("connections.id"), nullable=True)
    db_name: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft/deploying/running/stopped/destroyed
    entry_path: Mapped[str] = mapped_column(String(256), default="")
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    seed_data: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    # 字符串前向引用：PageSpec 在本文件后定义，SQLAlchemy 延迟解析
    pages: Mapped[list["PageSpec"]] = relationship(
        "PageSpec", back_populates="deployment", cascade="all, delete-orphan"
    )


class TableSpec(Base):
    __tablename__ = "table_specs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    deployment_id: Mapped[int] = mapped_column(ForeignKey("deployments.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    biz_name: Mapped[str] = mapped_column(String(128), default="")
    columns_json: Mapped[str] = mapped_column(Text, default="[]")
    ddl_sql: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="applied")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    @property
    def columns(self) -> list[dict]:
        return json.loads(self.columns_json or "[]")


class PageSpec(Base):
    __tablename__ = "page_specs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    deployment_id: Mapped[int] = mapped_column(ForeignKey("deployments.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # crud_list/public_form/dashboard
    access: Mapped[str] = mapped_column(String(8), default="member")  # public/member
    table_id: Mapped[int | None] = mapped_column(ForeignKey("table_specs.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(128), default="")
    layout_json: Mapped[str] = mapped_column(Text, default="{}")

    deployment: Mapped[Deployment] = relationship(back_populates="pages")


class CrudConfig(Base):
    __tablename__ = "crud_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    deployment_id: Mapped[int] = mapped_column(ForeignKey("deployments.id"), nullable=False)
    table_id: Mapped[int] = mapped_column(ForeignKey("table_specs.id"), nullable=False)
    api_config_json: Mapped[str] = mapped_column(Text, default="{}")
    ui_config_json: Mapped[str] = mapped_column(Text, default="{}")


class DeploymentMenu(Base):
    __tablename__ = "deployment_menus"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    deployment_id: Mapped[int] = mapped_column(ForeignKey("deployments.id"), nullable=False)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("deployment_menus.id"), nullable=True)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), default="table")  # table/flow/dashboard（渲染提示）
    ref_id: Mapped[int | None] = mapped_column(ForeignKey("page_specs.id"), nullable=True)  # → page_specs.id（闭环）


class DeploymentMenuGrant(Base):
    __tablename__ = "deployment_menu_grants"

    menu_id: Mapped[int] = mapped_column(ForeignKey("deployment_menus.id"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)


class DeploymentMember(Base):
    __tablename__ = "deployment_members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    deployment_id: Mapped[int] = mapped_column(ForeignKey("deployments.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(16), default="operator")  # creator/admin/operator


class FlowSpec(Base):
    __tablename__ = "flow_specs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    deployment_id: Mapped[int] = mapped_column(ForeignKey("deployments.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    steps_json: Mapped[str] = mapped_column(Text, default="[]")  # [{page_id, done_when, status_field}]
    version: Mapped[int] = mapped_column(Integer, default=1)

    @property
    def steps(self) -> list[dict]:
        return json.loads(self.steps_json or "[]")


class ActivityLog(Base):
    __tablename__ = "activity_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    deployment_id: Mapped[int | None] = mapped_column(ForeignKey("deployments.id"), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)  # NULL=系统
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/committed（DR-03）
    object_kind: Mapped[str] = mapped_column(String(16), default="")
    object_table: Mapped[str] = mapped_column(String(64), default="")
    object_pk: Mapped[str] = mapped_column(String(64), default="")
    object_label: Mapped[str] = mapped_column(String(256), default="")
    changes_json: Mapped[str] = mapped_column(Text, default="{}")  # {字段: {old, new}}
    source_ip: Mapped[str] = mapped_column(String(64), default="")

    @property
    def changes(self) -> dict:
        return json.loads(self.changes_json or "{}")


class AiGeneration(Base):
    __tablename__ = "ai_generations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), default="table")  # table/deploy/crud/qa/seed
    status: Mapped[str] = mapped_column(String(16), default="ok")
    prompt_summary: Mapped[str] = mapped_column(Text, default="")
    result_summary: Mapped[str] = mapped_column(Text, default="")
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class LlmModel(Base):
    __tablename__ = "llm_models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    base_url: Mapped[str] = mapped_column(String(256), default="")
    api_key_cipher: Mapped[str] = mapped_column(Text, default="")  # 加密存储
    temperature: Mapped[float] = mapped_column(default=0.0)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


def create_all_lazy(app) -> None:
    """惰性建表：启动时（或测试内显式调用）建齐全部元数据表。

    - checkfirst=True：幂等，已存在的表跳过（对齐 superset-lite 手法，不引 alembic）。
    - 元数据库与业务库是两个库：这里只建元数据库的表。
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    uri = app.config["SQLALCHEMY_DATABASE_URI"]
    if uri.startswith("sqlite:///./"):
        # 相对路径 sqlite：相对当前工作目录，测试里用 tmp_path 覆盖
        pass
    engine = create_engine(uri, future=True)
    Base.metadata.create_all(bind=engine, checkfirst=True)
    app.extensions["datamason_engine"] = engine
    app.extensions["datamason_session_factory"] = sessionmaker_bound(engine)


def sessionmaker_bound(engine):
    from sqlalchemy.orm import sessionmaker

    return sessionmaker(bind=engine, expire_on_commit=False, future=True)
