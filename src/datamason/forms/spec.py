"""表单控件配置规格（0.13.0 M7-0 / 0.15.0 M9-0）：控件类型、相容矩阵与配置载荷强校验。

0.15.0 M9-0 追加：switch/slider/rate/select/cascader 五类控件、控件参数（ControlConfigSpec）、
候选项层级（parent_value）。

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
    # 0.15.0 M9-0：新增五类（均 antd 内置组件，零新增依赖）
    "switch",
    "slider",
    "rate",
    "select",
    "cascader",
)

# 携带参数的控件集合（0.15.0 M9-0）：其余控件不允许配置 control_config
PARAM_CONTROLS = frozenset({"slider", "rate", "cascader"})

# 数据列类型族（与 src/datamason/ddl/table_spec.py 的 ColumnType 白名单一致）
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
    # 0.15.0 M9-0：新增五类（switch 固定 1/0；slider/rate 数值族；select 同 radio；cascader 字符串族）
    "switch": frozenset({"BOOLEAN"}),
    "slider": _NUMERIC_TYPES,
    "rate": _NUMERIC_TYPES,
    "select": _STRING_TYPES | _NUMERIC_TYPES | frozenset({"BOOLEAN"}),
    "cascader": _STRING_TYPES,
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
    """候选项：value=落库值，label=展示文案（均 ≤ 100 字符、非空）。

    parent_value（0.15.0 M9-0）：可空的父级 value，用于级联（cascader）层级表达；
    为空表示顶层。父引用合法性（存在/成环/自引用）在 FieldConfigSpec._validate 统一校验。
    """

    value: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=100)
    parent_value: str | None = Field(default=None, max_length=100)


class ControlConfigSpec(BaseModel):
    """控件参数（0.15.0 M9-0）：仅对 PARAM_CONTROLS（slider/rate/cascader）有意义。

    其余字段保持默认；参数与列类型的交叉校验（rate 半星须 DECIMAL、slider 整数列
    step 须正整数）由 assert_control_config_compatible 承担（需列类型上下文）。
    """

    min: float | None = None
    max: float | None = None
    step: float | None = None
    max_stars: int = Field(default=5, ge=1, le=10)
    allow_half: bool = False
    expand_trigger: Literal["click", "hover"] = "click"

    @model_validator(mode="after")
    def _validate(self) -> "ControlConfigSpec":
        if self.min is not None and self.max is not None and self.min >= self.max:
            raise ValueError("滑块最小值必须小于最大值")
        if self.step is not None and self.step <= 0:
            raise ValueError("滑块步长必须为正数")
        return self


def assert_control_config_compatible(
    data_type: str,
    control_type: str | None,
    config: ControlConfigSpec | None,
) -> None:
    """校验控件参数与列类型的交叉约束（保存时由服务层对照列类型调用）。

    - rate.allow_half=True 要求列 DECIMAL（半星需小数落库）；
    - slider 对 INTEGER/BIGINT 列要求 step 为正整数（整数列不得走小数步长）。
    无参数（config 为 None）或非参数控件直接放行。
    """
    if config is None or control_type not in PARAM_CONTROLS:
        return
    t = normalize_data_type(data_type)
    if control_type == "rate" and config.allow_half and t not in ("DECIMAL", "NUMERIC"):
        raise ValueError("「允许半星」评分仅支持 DECIMAL 列")
    if control_type == "slider" and t in ("INTEGER", "BIGINT"):
        if config.step is not None and float(config.step) != int(config.step):
            raise ValueError("整数列的滑块步长必须为正整数")


class FieldConfigSpec(BaseModel):
    """单字段控件配置（control_type 为空表示未配置）。"""

    field_name: str = Field(min_length=1, max_length=128)
    control_type: str | None = None
    control_config: ControlConfigSpec | None = None
    time_granularity: Literal["date", "datetime"] | None = None
    readonly: bool = False
    hidden: bool = False
    hint: str = Field(default="", max_length=200)
    options: list[OptionItemSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate(self) -> "FieldConfigSpec":
        if self.control_type is not None and self.control_type not in CONTROL_TYPES:
            raise ValueError(f"未知控件类型：{self.control_type}")
        if self.control_config is not None and self.control_type not in PARAM_CONTROLS:
            raise ValueError(f"控件「{self.control_type}」不支持参数配置")
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
        _validate_option_hierarchy(self.options)
        return self


def _validate_option_hierarchy(options: list[OptionItemSpec]) -> None:
    """层级校验（0.15.0 M9-0）：parent_value 必须引用本字段另一选项、不成环、不自引用。

    - 每个非空 parent_value 必须存在于同字段其他选项的 value；
    - 不允许自引用（parent_value == value）与循环引用（沿父链回到路径内节点）。
    """
    values = {o.value for o in options}
    parent_map = {
        o.value: o.parent_value for o in options if o.parent_value is not None
    }
    for value, parent in parent_map.items():
        if parent == value:
            raise ValueError(f"候选项不能引用自己为父级：{value}")
        if parent not in values:
            raise ValueError(f"候选项的父级不存在：{parent}")
    # 成环检测：沿父链走，若回到路径内已访问节点则成环
    for start in parent_map:
        seen: set[str] = set()
        cur = start
        while cur in parent_map:
            if cur in seen:
                raise ValueError(f"候选项层级存在循环引用：{cur}")
            seen.add(cur)
            cur = parent_map[cur]


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