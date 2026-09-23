"""表单控件配置规格（0.13.0 M7-0）：控件类型、相容矩阵与配置载荷强校验。

设计纪律：LLM 只产 JSON 中间规格（M7-7 候选项生成），本模块只做确定性规格校验，
不含任何 LLM / 落库 / HTTP 逻辑（纯领域，可被服务与路由复用）。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

CONTROL_TYPES = (
    "text",
    "textarea",
    "number",
    "boolean",
    "radio",
    "checkbox",
    "date",
    "datetime",
    "ref",
    "file",
)

# 数据列类型族（与 server/ddl/table_spec.py 的 ColumnType 白名单一致）
_STRING_TYPES = frozenset({"VARCHAR", "TEXT", "CHAR", "STRING", "ENUM"})
_NUMERIC_TYPES = frozenset({"INTEGER", "BIGINT", "DECIMAL"})

# 控件类型 → 允许的数据列类型集合（"ref" 单独按外键判定，不入表）
CONTROL_COMPAT: dict[str, frozenset[str]] = {
    "text": _STRING_TYPES,
    "textarea": _STRING_TYPES,
    "number": _NUMERIC_TYPES,
    "boolean": frozenset({"BOOLEAN"}),
    "radio": _STRING_TYPES | _NUMERIC_TYPES | frozenset({"BOOLEAN"}),
    "checkbox": _STRING_TYPES | _NUMERIC_TYPES | frozenset({"BOOLEAN"}),
    "date": frozenset({"DATETIME", "VARCHAR"}),
    "datetime": frozenset({"DATETIME", "VARCHAR"}),
    "file": frozenset({"FILE"}),
}

MAX_OPTIONS_PER_FIELD = 200
MAX_GROUPS = 20
COLUMNS_PER_ROW_CHOICES = (1, 2, 3)
PAGE_KINDS = ("workspace", "public")
TIME_GRANULARITIES = ("date", "datetime")


def normalize_data_type(data_type: str) -> str:
    """把列类型归一到白名单族（容忍 'VARCHAR(255)' 这类带参写法）。"""
    t = (data_type or "").upper().strip()
    if not t:
        return ""
    base = t.split("(", 1)[0].strip()
    if base in ("CHARACTER VARYING", "NVARCHAR", "NCHAR"):
        base = "VARCHAR"
    return base


def assert_control_compatible(
    data_type: str, control_type: str, *, is_fk: bool = False
) -> None:
    """校验控件类型与字段数据类型是否相容；不相容抛 ValueError（业务可读消息）。"""
    if control_type not in CONTROL_TYPES:
        raise ValueError(f"未知控件类型：{control_type}")
    if control_type == "ref":
        if not is_fk:
            raise ValueError("该字段不是外键，不能使用「引用选择」控件")
        return
    allowed = CONTROL_COMPAT.get(control_type, frozenset())
    if normalize_data_type(data_type) not in allowed:
        raise ValueError(f"数据类型 {data_type} 不支持「{control_type}」控件")


def default_control_type(data_type: str, *, is_fk: bool = False) -> str:
    """零配置时的默认控件映射（与升级前表单渲染行为保持一致）。"""
    if is_fk:
        return "ref"
    t = normalize_data_type(data_type)
    if t == "FILE":
        return "file"
    if t == "BOOLEAN":
        return "boolean"
    if t in _NUMERIC_TYPES:
        return "number"
    if t in ("DATETIME", "TIMESTAMP", "DATE"):
        return "datetime"
    if t in ("TEXT", "CLOB"):
        return "textarea"
    return "text"


class OptionItemSpec(BaseModel):
    """候选项：value=落库值，label=展示文案（均 ≤ 100 字符、非空）。"""

    value: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=100)


class FieldConfigSpec(BaseModel):
    """单字段控件配置（control_type 为空表示未配置）。"""

    field_name: str = Field(min_length=1, max_length=128)
    control_type: str | None = None
    time_granularity: Literal["date", "datetime"] | None = None
    readonly: bool = False
    hidden: bool = False
    hint: str = Field(default="", max_length=200)
    options: list[OptionItemSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate(self) -> "FieldConfigSpec":
        if self.control_type is not None and self.control_type not in CONTROL_TYPES:
            raise ValueError(f"未知控件类型：{self.control_type}")
        if self.time_granularity is not None and self.control_type not in (
            "date",
            "datetime",
        ):
            raise ValueError("时间粒度仅适用于日期/日期时间控件")
        if len(self.options) > MAX_OPTIONS_PER_FIELD:
            raise ValueError(f"单字段候选项最多 {MAX_OPTIONS_PER_FIELD} 个")
        seen: set[str] = set()
        for item in self.options:
            if item.value in seen:
                raise ValueError(f"候选项存储值重复：{item.value}")
            seen.add(item.value)
        return self


class OrderEntrySpec(BaseModel):
    """布局顺序项：显式声明字段展示次序。"""

    field_name: str = Field(min_length=1, max_length=128)


class GroupSpec(BaseModel):
    """字段分组：标题 ≤ 50 字符，成员字段名列表。"""

    title: str = Field(min_length=1, max_length=50)
    field_names: list[str] = Field(default_factory=list)


class LayoutSpec(BaseModel):
    """表单布局：列数 ∈ {1,2,3}、分组 ≤ 20。"""

    columns_per_row: int = 1
    order: list[OrderEntrySpec] = Field(default_factory=list)
    groups: list[GroupSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate(self) -> "LayoutSpec":
        if self.columns_per_row not in COLUMNS_PER_ROW_CHOICES:
            raise ValueError("每行列数只能是 1 / 2 / 3")
        if len(self.groups) > MAX_GROUPS:
            raise ValueError(f"分组最多 {MAX_GROUPS} 组")
        return self


class FormConfigPayload(BaseModel):
    """一次保存提交的完整表单配置载荷（字段配置 + 可选布局）。"""

    fields: list[FieldConfigSpec] = Field(default_factory=list)
    layout: LayoutSpec | None = None

    @model_validator(mode="after")
    def _validate(self) -> "FormConfigPayload":
        seen: set[str] = set()
        for f in self.fields:
            if f.field_name in seen:
                raise ValueError(f"字段配置重复：{f.field_name}")
            seen.add(f.field_name)
        return self