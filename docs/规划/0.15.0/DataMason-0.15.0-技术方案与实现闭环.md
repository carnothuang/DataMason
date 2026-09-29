# DataMason 0.15.0 技术方案与实现闭环 — 更丰富的表单控件、显文存码映射与页面级布局

> **需求来源**：`docs/规划/0.15.0/DataMason-0.15.0-版本规划.md`（§三 能力设计）。
> **基线**：0.14.0（M8 专业建库已交付并验收，离线 417 passed/4 skipped、真浏览器 E2E 全绿）。
> **视角**：技术方案（How）。需求（What）见版本规划；任务拆分见同目录《开发计划》。
> **裁决**：DR-46~53 已于 2026-09-29 回写《产品一页纸》§九·七，本方案是其工程落点。
> **纪律约束**：LLM 只产 JSON 中间规格；业务库零 DDL；显文存码只做读侧渲染不改存储与写路径；页面布局 `"{}"`=默认派生（零配置零变化）；直连模式只读红线不变；交付线为离线 `pytest` 全绿 + `npm --prefix dashboard run build` 通过。

---

# 一、需求与存量功能关系分析

## 1.1 已实现、可直接复用的存量能力

| 需求功能 | 存量功能 | 代码位置 | 匹配度 |
|---------|---------|---------|--------|
| 控件类型枚举 / 相容矩阵 / 默认映射 / 配置载荷强校验 | `CONTROL_TYPES` / `CONTROL_COMPAT` / `default_control_type` / pydantic 载荷 | `src/datamason/forms/spec.py`（全文 172 行） | 100%（本版追加） |
| 表单配置三表（字段控件/候选项/布局）与读写/求交/降级 | `get_form_config` / `save_form_config` / `_assemble` | `src/datamason/forms/service.py` | 100% |
| 写路径值校验注入（radio/checkbox/date） | `_validate_value` / `build_validator` / `build_registry_validator` | `src/datamason/forms/service.py` | 75%（本版追加新控件校验） |
| 运行态只读配置接口 | `GET /app/<id>/api/forms/<table>`（任意成员） | `src/datamason/forms/routes.py:109-114` | 100% |
| 配置读写权限（创建人/平台管理员） | `_require_form_config` | `src/datamason/forms/routes.py:57-69` | 100%（布局配置沿用） |
| 外键显示列推断 | `list_refs` 复用 `_label_column` / `_pk_column` | `src/datamason/crud/engine.py:556-577` | 100%（FK 批量标签复用同一口径） |
| 列表数据装配（分页/排序/过滤/归属过滤） | `list_rows` 返回 `{data,page,page_size,total}` | `src/datamason/crud/engine.py:449-492` | 100%（FK 批量标签在路由层补） |
| 元数据库惰性建表 + 缺列回填（无 alembic） | `create_all_lazy` + `_backfill_missing_columns` | `src/datamason/models/__init__.py:335-397` | 100%（加列沿用） |
| 页面规格与表级 UI 配置占位列 | `page_specs.layout_json` / `crud_configs.ui_config_json`（Text, default "{}"，自建库起存在、无人消费） | `src/datamason/models/__init__.py:104-128` | 100%（本版首次消费） |
| 前端渲染唯一真相源（控件分支 + 降级 + 预览） | `FormControlRenderer`（`CONTROL_LABELS` / `compatibleControls` / `defaultControlType` / `renderFormControl` / `renderColumnPreview`） | `dashboard/src/components/FormControlRenderer.tsx` | 100%（追加分支） |
| 表单设计器（控件/候选项/只读隐藏/顺序分组/实时预览） | `FormDesigner` | `dashboard/src/pages/workspace/FormDesigner.tsx` | 75%（追加控件与参数） |
| 抽屉表单按配置渲染 | `CrudForm` 调 `FormsApi.runtimeConfig`（失败静默降级） | `dashboard/src/pages/workspace/CrudForm.tsx` | 100% |
| 前端 API 单文件（类型 + 分组） | `FormsApi` / `FormControlType` / `FormConfigResponse` | `dashboard/src/api.ts:934-1038` | 100%（扩展类型） |
| 三层角色 + 按人授权（布局配置权限判定） | `get_role` / `assert_member` / `require_role` / `table_access_map` | `src/datamason/members/service.py` | 100% |
| 两阶段操作日志 + 系统动作日志（配置变更留痕） | `AuditSession` / `log_system` / `reconcile_pending` | `src/datamason/audit/service.py` | 100%（布局配置写 `ui_config` 动作） |
| 页面卡 / 菜单树装载 | `build_payload` 返回 `menus` + `pages`（kind/access/table） | `src/datamason/workspace/service.py:66-122` | 100%（页面携带布局） |
| 配置包导出 | `build_package`（deploy/export.py） | `src/datamason/deploy/export.py` | 100%（布局随 page_specs/crud_configs 已含） |

## 1.2 需扩展的存量能力

| 需求功能 | 存量功能 | 差异说明 | 扩展方向 |
|---------|---------|---------|---------|
| 新控件（switch/slider/rate/select/cascader）+ 控件参数 | `CONTROL_TYPES` 10 类、无参数概念 | **结构差异**：缺参数载体；相容矩阵需补 5 类 | spec 追加类型与 `ControlConfigSpec`；`form_field_configs` 加参数 JSON 列；`compatibleControls`/`defaultControlType` 同步 |
| 层级候选项 | `form_option_items` 扁平 value/label | **结构差异**：无父级概念 | 表加 `parent_value` 列；spec 校验父引用/成环 |
| 新控件写校验 | `_validate_value` 仅 radio/checkbox/date | **逻辑差异**：缺 slider/rate/select/cascader/switch | service 追加校验分支，`build_validator` 透传参数 |
| 列表/筛选/详情回显 label | `CrudListPage` 直接 `String(v)`（139-146 行），未拉 FormsApi | **逻辑差异**：无码→文转换 | CrudListPage 拉 runtimeConfig + rows 响应 `ref_labels`，回显转换 + 兜底 |
| FK 列表还原名称（无 N+1） | 仅录入时 `list_refs` 远程搜索 | **逻辑差异**：列表侧无批量标签 | rows 路由装配 `ref_labels`（IN 批量），复用 `_label_column` |
| 页面级布局（列显隐/列宽/行操作/默认视图/详情/统计/自由拖拽） | `page_specs.layout_json`/`crud_configs.ui_config_json` 空占位 | **结构差异**：无 schema、无读写、无渲染 | 新增布局 schema + 读写接口 + 画布设计器（craft.js+RGL）+ 运行态渲染器 |
| 列表默认视图可配 | 卡片/表格切换仅 `localStorage` 全局 | **逻辑差异**：无每表默认 | `ui_config_json` 增加 default_view，页面装载时读取 |

## 1.3 需新增的资产

| 资产 | 类型 | 用途 |
|------|------|------|
| `craft.js`（`@craftjs/core`） | 前端依赖（MIT，DR-52） | 页面画布节点树 + 物料/属性面板框架 |
| `react-grid-layout` | 前端依赖（MIT，DR-52） | 区块自由拖拽/缩放栅格坐标 |
| `LayoutDesigner` | 前端页面 | 页面布局画布设计器 |
| `page_specs.layout_json` / `crud_configs.ui_config_json` 消费逻辑 | 后端 + 前端 | 页面级/表级布局存储与渲染 |
| `PageLayoutService`（新服务函数） | 后端 | 布局读写/校验/默认派生 |
| `ref_labels` 装配 | 后端 | FK 批量标签随 rows 返回 |

---

# 二、领域规格扩展（forms/spec.py）

## 2.1 控件类型与相容矩阵

```python
CONTROL_TYPES = ("text", "textarea", "number", "boolean", "radio", "checkbox",
                 "date", "datetime", "ref", "file",
                 "switch", "slider", "rate", "select", "cascader")  # +5

CONTROL_COMPAT = {
    # ...既有 10 类不变...
    "switch":   frozenset({"BOOLEAN"}),
    "slider":   _NUMERIC_TYPES,                      # INTEGER/BIGINT/DECIMAL
    "rate":     _NUMERIC_TYPES,                      # 整数为主；allow_half 时需 DECIMAL
    "select":   _STRING_TYPES | _NUMERIC_TYPES | frozenset({"BOOLEAN"}),  # 同 radio
    "cascader": _STRING_TYPES,                       # 末级 value 为字符串
}
```

- `default_control_type` 不改（零配置仍走原默认：boolean→boolean 下拉、数值→number 等），保证存量表单外观不变。
- `compatibleControls`（前端）与后端矩阵用同一组样本对齐，禁止单边新增。

## 2.2 控件参数（ControlConfigSpec）

```python
class ControlConfigSpec(BaseModel):
    min: float | None = None          # slider
    max: float | None = None          # slider
    step: float | None = None         # slider（整数列必须为正整数）
    max_stars: int = 5                # rate，1..10
    allow_half: bool = False          # rate；True 时列必须 DECIMAL
    expand_trigger: Literal["click", "hover"] = "click"  # cascader

    @model_validator(mode="after")
    def _validate(self) -> "ControlConfigSpec":
        if self.min is not None and self.max is not None and self.min >= self.max:
            raise ValueError("滑块最小值必须小于最大值")
        if self.step is not None and self.step <= 0:
            raise ValueError("滑块步长必须为正数")
        if not (1 <= self.max_stars <= 10):
            raise ValueError("评分星数须在 1~10 之间")
        return self
```

`FieldConfigSpec` 增加 `control_config: ControlConfigSpec | None = None`；按控件类型做强校验（如 `rate.allow_half=True` 时校验目标列 data_type 为 DECIMAL、`slider` 对 INTEGER/BIGINT 列校验 step 为整数）。

## 2.3 层级候选项

`OptionItemSpec` 增加 `parent_value: str | None = None`（可空，≤100）。`FieldConfigSpec._validate` 追加：

- 每个非空 `parent_value` 必须引用本字段内另一选项的 value；
- 沿 parent 链不得成环（自引用或循环）；
- 同层（同 parent_value）value 仍唯一（既有 value 唯一校验保留）。

---

# 三、元数据库模型变更（幂等，无 alembic）

仅元数据库、零新表，沿用 `create_all_lazy(checkfirst=True)` + `_backfill_missing_columns`：

| 表 | 变更 | 说明 |
|----|------|------|
| `form_field_configs` | 新增 `control_config_json TEXT DEFAULT '{}'`（可空） | 控件参数载体；幂等 `ALTER TABLE ADD COLUMN` |
| `form_option_items` | 新增 `parent_value VARCHAR(100) NULL` | 层级父值；幂等补列 |
| `page_specs` / `crud_configs` | 无新增列 | `layout_json` / `ui_config_json` 自建库起存在，本版消费 |

回填逻辑在 `models/__init__.py` 的 `_backfill_missing_columns` 补两列即可（幂等）；老库升级自动补列、默认空值，零配置行为不变。业务库零 DDL。

---

# 四、写校验扩展（forms/service）

`_validate_value` 追加分支（确定性校验，不调 LLM）：

| 控件 | 校验 | 失败消息示例 |
|------|------|------|
| switch | 值 ∈ {1, 0}（沿用 BOOLEAN 口径） | "「{biz}」的值只能是 1 或 0" |
| slider | 数值落在 [min, max]、满足 step、类型与列一致（整数列拒绝小数） | "「{biz}」须在 {min}~{max} 之间且为 {step} 的倍数" |
| rate | 1..max_stars；allow_half=false 整数、true 允许 0.5 倍数 | "「{biz}」评分须在 1~{max} 之间" |
| select | 值 ∈ 候选项 value 集合 | "「{biz}」的选项无效" |
| cascader | 值 ∈ 候选项**末级** value 集合（父节点 value 不可提交） | "「{biz}」须选择末级选项" |

`build_validator` / `build_registry_validator` 透传 `control_config`；所有写路径（工作台 / 公开表单 / API key）统一生效。

---

# 五、FK 批量标签解析（M9-B 后端落点）

## 5.1 现状

- 录入侧：`FkSelect` 经 `list_refs` 远程搜索，已有 label。
- 列表侧：`/app/<id>/api/tables/<name>/rows` 直接返回 `list_rows` 原始值（`workspace/routes.py:124-130`），无标签。

## 5.2 设计

在 **workspace rows 路由层**补装配，不改 `crud/engine.list_rows` 签名（保持纯领域）：

```
GET /app/<id>/api/tables/<name>/rows?page=1&page_size=20...
→ list_rows(registry, name)           # 既有，返回 {data,page,page_size,total}
→ attach_ref_labels(registry, name, data)   # 新增纯函数
→ 响应增加 "ref_labels": { "<fk_col>": { "<id>": "label" } }
```

`attach_ref_labels`（可放 `workspace/service.py`）：

1. 取 `registry.table(name)` 的列，筛出**外键列**（`col.foreign_keys` 非空）且排除自引用表；过滤归属列。
2. 对每列收集当前页 data 中该列去重非空 id。
3. 按列分别执行一次 `SELECT <pk>, <label_col> FROM <target> WHERE <pk> IN (...)`（`label_col` 复用 `_label_column` 推断：名称/标题类列优先，回退主键）。
4. 结果组装 `ref_labels`；查不到的 id 不放入映射（前端兜底原值）。
5. 业务库引擎由路由层 `finally dispose`（沿用 `_deployment_api` 既有链路）。

约束：仅当前页、仅可见 FK 列、id 去重，每页 20 行 → 查询次数 = 可见 FK 列数（有界，非 N+1）。直连（link）模式为只读 SELECT，不触碰 DR-27。

## 5.3 公开表单一致性

公开表单录入已有 label；公开页布局安全子集复用同一回显口径。

---

# 六、页面布局 schema 与接口（M9-C 后端）

## 6.1 页面级布局（`page_specs.layout_json`）

Schema 写法参考 Formily Schema 2.0 协议方言（`x-component` 等），**不引入 formily 运行时**，由自研渲染器消费：

```jsonc
{
  "version": 1,
  "blocks": [
    {
      "key": "b1",
      "x-component": "table",          // table | detail | stat | html
      "props": {
        "table": "orders",             // 引用表名（table/detail/stat）
        "columns": ["no", "customer_id", "amount", "status"],  // 列显隐/列序
        "colWidths": { "amount": 160 },
        "showActions": true,           // 行操作按钮区显隐
        "defaultView": "table"         // table | card
      },
      "layout": { "x": 0, "y": 0, "w": 12, "h": 8 }   // RGL 栅格坐标
    },
    { "key": "b2", "x-component": "stat",
      "props": { "table": "orders", "aggregation": "sum", "column": "amount",
                 "title": "订单总额", "filter": "" } },
    { "key": "b3", "x-component": "html",
      "props": { "content": "## 说明\n- 本页为订单台账" } }
  ]
}
```

- `detail` 区块复用 `crud_configs.ui_config_json` 的详情字段配置（见 6.2）或内联 `fields`。
- `"{}"` = 未配置 → 运行态走 `_derive_pages` 默认派生行为。

## 6.2 表级 UI 配置（`crud_configs.ui_config_json`）

```jsonc
{
  "version": 1,
  "list": {
    "visibleColumns": ["no", "customer_id", "amount", "status"],
    "colWidths": { "amount": 160 },
    "showActions": true,
    "defaultView": "card",
    "showFilters": true
  },
  "detail": {
    "fields": ["customer_id", "status", "amount"],
    "groups": [ { "title": "基础", "field_names": ["customer_id", "status"] } ],
    "columns_per_row": 2
  }
}
```

## 6.3 接口契约

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| GET | `/deployments/<id>/pages/<page_id>/layout` | 创建人/平台管理员 | 读页面级布局 |
| PUT | `/deployments/<id>/pages/<page_id>/layout` | 创建人/平台管理员 | 保存页面级布局（pydantic 校验 + 区块引用表/列存在性 + stat 聚合口径） |
| GET | `/deployments/<id>/tables/<table>/ui-config` | 创建人/平台管理员 | 读表级 UI 配置 |
| PUT | `/deployments/<id>/tables/<table>/ui-config` | 创建人/平台管理员 | 保存表级 UI 配置 |
| GET | `/app/<id>/api/pages/<page_id>/layout` | 任意成员 | **运行态只读**布局（含表级 UI 配置合并），供页面渲染 |
| DELETE | `/deployments/<id>/pages/<page_id>/layout` | 创建人/平台管理员 | 还原默认（清回 `"{}"`） |

- 校验：区块 `x-component` ∈ 有限集；`table` 引用存在；`columns` 为引用表既有列；`stat` 聚合 ∈ {count,sum,avg} 且 sum/avg 目标列必须为数值列。
- 权限：沿用 `_require_form_config` 语义（创建人/平台管理员可配置；operator 只读运行态结果，对应 §八 Q4）。
- 审计：写布局落 `activity_logs`（action=`ui_config`），沿用两阶段日志。
- 导出：`layout_json`/`ui_config_json` 已随 `page_specs`/`crud_configs` 进配置包，无需改 `export.py`。

---

# 七、页面布局画布（M9-C 前端，craft.js + react-grid-layout）

## 7.1 依赖与分层（DR-52）

```
运行态渲染层（自研渲染器，零设计器依赖）      ← 打包进主应用
        ▲ 消费 layout_json / ui_config_json（读侧纯渲染）
        │
设计器层（动态 import，仅「布局」入口加载）    ← 按需 chunk
  ├─ craft.js（@craftjs/core）   管 区块内字段/属性编排（节点树）
  └─ react-grid-layout（RGL）    管 区块在页面的位置/缩放（栅格坐标）
```

- 运行态渲染与 craft.js 解耦：渲染器只认 `layout_json` schema，不依赖 craft 运行时 → 若 craft.js 停更可整体替换为 dnd-kit 自研，渲染层零改动。
- 体积：设计器 `import()` 按需加载（React.lazy + Suspense），不进入首屏主 chunk；构建时在验收里记录体积增量。

## 7.2 设计器能力（`dashboard/src/pages/workspace/LayoutDesigner.tsx`）

| 能力 | 实现 |
|------|------|
| 物料面板拖入区块 | 物料项 = table/detail/stat/html 四类；droppable 到画布 |
| 区块自由摆放/缩放 | RGL `Layout`（x/y/w/h），compact 压实 |
| 区块内字段配置 | 复用 M7 表格化交互（列显隐/列序/列宽、详情分组、统计口径） |
| 实时预览 | 画布右侧渲染对应区块真实组件（走 mock / 真实 runtimeConfig） |
| 保存/还原 | 保存写 §6.3 接口；「还原默认」清空回 `"{}"` |
| 入口 | 列表页/详情页/公开页操作区「布局」按钮；`page_specs.kind=dashboard` 页直达画布 |

## 7.3 运行态渲染器（`dashboard/src/components/PageLayoutRenderer.tsx`）

- 输入：`layout_json` + `ui_config_json` + 该表 meta/ref_labels。
- 渲染：RGL `GridLayout` 定位区块；`table` 区块 = 现有 CrudListPage 的列渲染逻辑（受 ui_config 约束）；`detail` = 详情布局；`stat` = 只读聚合调用（新增后端接口或复用 rows + 前端聚合——**采用后端聚合**，避免拉全量数据，见 §8 接口）；`html` = 只读 Markdown/文本。
- 降级：schema 解析异常 → 整体退回默认渲染（`degraded`），绝不阻塞录数；`"{}"` 直接走默认。

## 7.4 新增后端聚合接口（stat 区块）

`GET /app/<id>/api/tables/<name>/stat?aggregation=sum&column=amount&filter=...` → `{value, title}`；`count` 无需列；复用 `_filtered` 过滤 + 归属过滤 + 只读 SELECT；直连模式同样只读。

---

# 八、前端渲染扩展（M9-A/M9-B）

## 8.1 FormControlRenderer（渲染唯一真相源）

- `CONTROL_LABELS` 追加 5 项（开关/滑块/评分/下拉选择/级联选择）。
- import 追加 antd `Switch` / `Slider` / `Rate` / `Select` / `Cascader`（零新包）。
- 渲染分支：
  - switch → `<Switch checked={v===1} checkedChildren=开 unCheckedChildren=关 />`（值 1/0 与后端 `_coerce_value` 对齐）；
  - slider → `<Slider min max step />`（INTEGER 列 `step={Math.max(1, Math.round(step))}`）；
  - rate → `<Rate count={max_stars} allowHalf={allow_half} />`；
  - select → `<Select options={options} />`（无选项降级禁用 Input，同 radio）；
  - cascader → `<Cascader options={tree} changeOnSelect={false} />`（树由 options + parent_value 组装，只允许选末级）。
- `compatibleControls` / `defaultControlType` 同步；`renderColumnPreview` 支持新控件预览。

## 8.2 CrudListPage（M9-B 回显 + M9-C 布局）

- 增加：拉 `FormsApi.runtimeConfig`（失败静默降级）→ 得到每字段 `options`/`control_config`；拉页面 layout（运行态只读）。
- 列渲染改造（替换现 139-146 行 `String(v)`）：
  - 选项类（radio/select/cascader/checkbox 有 options）→ value→label；cascader 沿 parent 拼完整路径；查不到 → 原值。
  - FK 列 → `ref_labels[col][String(v)] ?? v`。
  - 其余 → 原逻辑（FILE 用 FileChip，null 显示 `—`）。
- 筛选：静态选项字段（radio/select）改下拉（label 展示、value 提交）；FK 筛选维持现状（§八 Q3）。
- 布局：按 `ui_config_json.list` 渲染列显隐/列宽/操作按钮/默认视图；`defaultView` 读配置，用户手动切换即时生效（仍写 localStorage）。

## 8.3 公共展示组件

列表/详情对 value→label 的转换抽为 `dashboard/src/components/ValueLabel.tsx`（入参：value、controlType、options、refLabels）——唯一转换点，录入、列表、筛选、详情、公开页共用，避免四处分散。

---

# 九、数据流闭环

```
录入侧（已有 label）                列表/详情/筛选侧（本版补齐）
  radio/select/cascader ─┐            value ──► ValueLabel ──► label
  FK ──► FkSelect(label)  └─► 库存值 ──► ref_labels(IN 批量) ─┘
  switch/slider/rate ──► 数值/1/0         └─ 查不到 → 兜底原值（不报错）
        │ 写校验（§四）                          ▲
        └──► crud 写路径 ──► 业务库（始终存码）   └─ 读侧纯渲染，不改存储（DR-49）
```

---

# 十、测试点（pytest / 真浏览器）

**离线（pytest，新增 `tests/test_m9_*.py`，里程碑命名）**

1. spec：5 新控件相容矩阵（合法可配/非法拒绝）；参数校验（min≥max、整数列小数 step、半星非 DECIMAL、星数越界）；parent_value 悬空/成环/自引用拒绝、末级通过。
2. 写校验：switch 非 0/1、slider 越界/错步长、rate 超星/整数列半星、select/cascader 值不在集合、cascader 父节点提交——各拒绝；合法值通过。
3. 层级：value→完整路径；`_assemble` 返回层级树；降级路径（配置损坏 → degraded）。
4. FK 批量标签：`attach_ref_labels` 正确映射；断言查询次数 = 可见 FK 列数（用 mock engine 计数）；查不到 id 不抛错；link 模式只读。
5. 布局：`layout_json`/`ui_config_json` 读写/校验；非法区块类型/引用表/列/聚合口径拒绝；`"{}"` 走默认派生；存量空配置行为不变；还原默认清空。
6. stat 聚合：count/sum/avg 正确 + 归属过滤；sum/avg 对非数值列拒绝。
7. 回归：零配置字段渲染/校验/回显与 0.14 一致；直连只读。

**真浏览器（Playwright，`scripts/e2e/browser_e2e_m9.cjs`）**

1. 设计器配 5 类控件（含滑块范围步长、Rate 星数、三级级联）→ 保存 → 重开回显。
2. 录入控件提交 → **查业务库验证库存的是编码/数值/末级 id**（非标签）。
3. 列表/筛选显示中文标签与级联完整路径；FK 列显示引用表名称而非 id；构造选项外历史值 → 原样显示不报错。
4. 画布：拖入 table+stat+html 区块、自由拖拽摆放、配列显隐/列宽/隐藏操作按钮 → 保存 → 运行态按布局渲染、stat 数值正确。
5. 默认视图：某表配 defaultView=card → 重进列表即卡片。
6. 公开表单：5 类控件与显文存码生效；公开页安全子集生效、无拖拽入口。
7. 直连表：可配控件/布局并正确回显，全程无写入。

---

# 十一、风险与降级

| 风险 | 缓解 |
|------|------|
| craft.js 停滞 | DR-52 分层：渲染器与 craft.js 解耦，RGL 管坐标；可替换 dnd-kit 自研 |
| 画布体积 | 设计器 `React.lazy` 按需加载，不入首屏；验收记录体积增量 |
| 布局 schema 难读 | 保存归一化（排序/去重/约束 RGL 栅格）；「还原默认」一键清空 |
| 布局解析异常阻塞录数 | 运行态 `degraded` 降级默认渲染，绝不崩溃 |
| FK 批量查询成本 | 仅当前页/可见 FK/去重 id；复用索引主键 |
| 脏值/选项变更 | 读侧兜底原值（DR-49），不报错 |

---

*版本：0.15.0 技术方案 v1 ｜ 视角：工程（How）｜ 裁决：DR-46~53（已回写产品一页纸 §九·七）｜ 配套：同目录《版本规划》《开发计划》*
