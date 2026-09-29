"""table_spec：LLM 只产这个中间规格，确定性校验（设计细化 §十一·四）。

对齐「LLM 只产中间规格，Python 确定性映射」纪律：
- 类型白名单、单主键、列名不重复、外键引用存在且指向主键、无孤儿表、无重名表
- 校验失败由上层回喂 LLM 重试（上限 2 次）
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

# 三方言共同支持的逻辑类型白名单
# FILE（附件，0.11.0 DR-28）：确定性映射为 VARCHAR(255)，存 attachment id（或 id 数组 JSON），
# 二进制落每部署文件目录，不进业务库。
ColumnType = Literal[
    "INTEGER", "BIGINT", "VARCHAR", "TEXT", "BOOLEAN", "DATETIME", "DECIMAL", "FILE"
]


class ColumnSpec(BaseModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    biz_name: str = ""
    type: ColumnType
    length: int | None = Field(default=None, ge=1, le=4000)  # VARCHAR 长度
    nullable: bool = True
    default: str | int | float | bool | None = None
    is_pk: bool = False
    autoincrement: bool = False
    unique: bool = False  # 单列唯一（M2-4 唯一校验依据）
    is_fk: bool = False
    ref_table: str | None = None
    ref_column: str | None = None
    is_ownership: bool = False  # 归属列注入标记（created_by 等）

    @model_validator(mode="after")
    def _check_fk_target(self):
        if self.is_fk and (not self.ref_table or not self.ref_column):
            raise ValueError(f"外键列 {self.name} 必须提供 ref_table/ref_column")
        return self


class TableSpecModel(BaseModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    biz_name: str = ""
    columns: list[ColumnSpec] = Field(min_length=1)
    indexes: list[list[str]] = Field(default_factory=list)
    biz_notes: str = ""

    @property
    def pk_columns(self) -> list[ColumnSpec]:
        return [c for c in self.columns if c.is_pk]

    @property
    def column_names(self) -> set[str]:
        return {c.name for c in self.columns}

    @model_validator(mode="after")
    def _check_table(self):
        pks = [c for c in self.columns if c.is_pk]
        if len(pks) != 1:
            raise ValueError(f"表 {self.name} 必须且只能有一个主键列，当前 {len(pks)} 个")
        col_names = [c.name for c in self.columns]
        if len(col_names) != len(set(col_names)):
            raise ValueError(f"表 {self.name} 存在重名列")
        # 归属列为系统保留（DR-05，语义固定=users.id），禁止 LLM 占用做业务列/外键
        for c in self.columns:
            if c.name in ("created_by", "created_at", "updated_by", "updated_at") \
                    and not c.is_ownership:
                raise ValueError(
                    f"表 {self.name} 的列 {c.name} 是系统保留归属列，"
                    "请改名（如 submitter_id、replied_by），归属列由系统自动生成"
                )
        return self


class SchemaSpec(BaseModel):
    """一次部署的整库规格。"""

    tables: list[TableSpecModel] = Field(min_length=1)
    biz_notes: str = ""

    @property
    def table_map(self) -> dict[str, TableSpecModel]:
        return {t.name: t for t in self.tables}

    @model_validator(mode="after")
    def _validate_structure(self):
        names = [t.name for t in self.tables]
        if len(names) != len(set(names)):
            raise ValueError("存在重名表")

        tmap = self.table_map
        for table in self.tables:
            for c in table.columns:
                if not c.is_fk:
                    continue
                if c.ref_table not in names:
                    raise ValueError(
                        f"表 {table.name}.{c.name} 引用了不存在的表 {c.ref_table}"
                    )
                target = tmap[c.ref_table]
                if c.ref_column not in target.column_names:
                    raise ValueError(
                        f"外键 {table.name}.{c.name} 的引用列 {c.ref_table}.{c.ref_column} 不存在"
                    )
                if not any(pc.name == c.ref_column for pc in target.pk_columns):
                    raise ValueError(
                        f"MVP 外键仅允许引用主键：{table.name}.{c.name} → {c.ref_table}.{c.ref_column}"
                    )
            for idx in table.indexes:
                for col in idx:
                    if col not in table.column_names:
                        raise ValueError(f"表 {table.name} 索引引用了不存在的列 {col}")
        return self
