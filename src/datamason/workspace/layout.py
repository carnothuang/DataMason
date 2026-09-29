"""页面布局 schema（0.15.0 M9-6 / DR-53）：页面级与表级布局 JSON 的确定性规格校验。

- 页面级 `page_specs.layout_json`：页面 → 区块（table/detail/stat/html）→ 字段；
- 表级 `crud_configs.ui_config_json`：列表列显隐/列宽/操作按钮/默认视图 + 详情布局。
写法参考 Formily Schema 2.0 协议方言（`x-component` / `props` 风格），但不引入 formily
运行时，由自研渲染器消费；`"{}"`=未配置（走默认派生，零配置零变化）。

纯领域：不 import Flask 的 request/g/session；表/列存在性等需要查库的校验在
`service.py` 对照 table_specs 完成（本模块只做结构与枚举校验）。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

PAGE_LAYOUT_VERSION = 1
UI_CONFIG_VERSION = 1

BLOCK_TYPES = ("table", "detail", "stat", "html")
AGGREGATIONS = ("count", "sum", "avg")
VIEW_CHOICES = ("table", "card")

MAX_BLOCKS = 20


class BlockLayoutSpec(BaseModel):
    """区块在页面的栅格坐标（RGL 布局）。"""

    x: int = Field(default=0, ge=0)
    y: int = Field(default=0, ge=0)
    w: int = Field(default=12, ge=1, le=24)
    h: int = Field(default=4, ge=1)


class TableBlockProps(BaseModel):
    """列表表格区块：引用既有表的列清单与展示口径。"""

    table: str = Field(min_length=1, max_length=128)
    columns: list[str] = Field(default_factory=list)  # 空 = 全部可见
    col_widths: dict[str, int] = Field(default_factory=dict)
    show_actions: bool = True
    default_view: Literal["table", "card"] = "table"
    show_filters: bool = True


class StatBlockProps(BaseModel):
    """统计卡区块：count / sum / avg 单值聚合。"""

    table: str = Field(min_length=1, max_length=128)
    aggregation: Literal["count", "sum", "avg"] = "count"
    column: str | None = None  # sum/avg 必填，且须为数值列（service 校验）
    title: str = Field(default="", max_length=100)
    filter: str = Field(default="", max_length=200)  # 预留：附加过滤表达式（本版仅占位）

    @model_validator(mode="after")
    def _validate(self) -> "StatBlockProps":
        if self.aggregation in ("sum", "avg") and not self.column:
            raise ValueError("sum/avg 聚合必须指定目标列")
        return self


class HtmlBlockProps(BaseModel):
    """自定义内容区块：只读文本/Markdown。"""

    content: str = Field(default="", max_length=4000)


class BlockSpec(BaseModel):
    """单个页面区块。"""

    key: str = Field(min_length=1, max_length=32, pattern=r"^[a-zA-Z0-9_-]+$")
    x_component: Literal["table", "detail", "stat", "html"]
    props: dict = Field(default_factory=dict)
    layout: BlockLayoutSpec | None = None


class PageLayoutSpec(BaseModel):
    """页面级布局（`page_specs.layout_json`）。"""

    version: Literal[1] = PAGE_LAYOUT_VERSION
    blocks: list[BlockSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate(self) -> "PageLayoutSpec":
        if len(self.blocks) > MAX_BLOCKS:
            raise ValueError(f"单页区块最多 {MAX_BLOCKS} 个")
        seen: set[str] = set()
        for b in self.blocks:
            if b.key in seen:
                raise ValueError(f"区块 key 重复：{b.key}")
            seen.add(b.key)
            if b.x_component == "table":
                TableBlockProps.model_validate(b.props)
            elif b.x_component == "stat":
                StatBlockProps.model_validate(b.props)
            elif b.x_component == "html":
                HtmlBlockProps.model_validate(b.props)
            # detail 区块：字段配置在表级 ui_config.detail，本层无额外 props
        return self


class ListUiSpec(BaseModel):
    """表级列表配置（`crud_configs.ui_config_json.list`）。"""

    visible_columns: list[str] = Field(default_factory=list)  # 空 = 全部可见
    col_widths: dict[str, int] = Field(default_factory=dict)
    show_actions: bool = True
    default_view: Literal["table", "card"] = "table"
    show_filters: bool = True


class DetailGroupSpec(BaseModel):
    """详情字段分组。"""

    title: str = Field(min_length=1, max_length=50)
    field_names: list[str] = Field(default_factory=list)


class DetailUiSpec(BaseModel):
    """表级详情配置（`crud_configs.ui_config_json.detail`）。"""

    fields: list[str] = Field(default_factory=list)  # 空 = 全部可见
    groups: list[DetailGroupSpec] = Field(default_factory=list)
    columns_per_row: int = Field(default=2, ge=1, le=3)


class TableUiConfig(BaseModel):
    """表级 UI 配置（`crud_configs.ui_config_json`）。"""

    version: Literal[1] = UI_CONFIG_VERSION
    list: ListUiSpec = Field(default_factory=ListUiSpec)
    detail: DetailUiSpec = Field(default_factory=DetailUiSpec)
