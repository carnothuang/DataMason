# DataMason 0.15.0 开发计划 — 编码任务规划（M9）

> **需求来源**：`docs/规划/0.15.0/DataMason-0.15.0-版本规划.md`（What）。
> **技术方案**：`docs/规划/0.15.0/DataMason-0.15.0-技术方案与实现闭环.md`（How）。
> **基线**：0.14.0（M8 专业建库已交付并验收，417 passed/4 skipped、真浏览器 E2E 全绿）。
> **视角**：任务拆分（When / Who）。需求（What）见版本规划；技术方案（How）见技术方案与实现闭环。
> **交付线**：离线 `.\.venv\Scripts\python.exe -m pytest` 全绿 + `npm --prefix dashboard run build` 通过。
> **工程纪律**：表单控件用 antd 内置（零新包）；页面布局画布引入 **craft.js + react-grid-layout**（DR-52，唯一受控放开）；不引入 alembic（沿用 `create_all_lazy` + 列回填）；LLM 只产 JSON 中间规格；业务库**零 DDL**；显文存码只做读侧渲染不改存储；每个任务一次 commit（信息含任务号），全绿再进下一项。

---

## 一、任务总览

| 任务 | 内容 | 主要落点 | 验收方式 |
|------|------|---------|---------|
| **M9-0 控件规格扩展** | 5 类新控件 + 控件参数 + 层级候选项 + 元数据库加列 | `src/datamason/forms/spec.py`、`src/datamason/models/__init__.py` | 离线 pytest |
| **M9-1 写校验扩展** | slider/rate/select/cascader/switch 校验注入三条写路径 | `src/datamason/forms/service.py`、`src/datamason/crud/` | 离线 pytest |
| **M9-2 后端映射服务** | 读配置回传参数与层级树；rows 装配 FK 批量 ref_labels | `src/datamason/forms/service.py`、`src/datamason/workspace/service.py`、`src/datamason/workspace/routes.py` | 离线 pytest |
| **M9-3 前端控件渲染** | FormControlRenderer 接入 5 类控件 + 参数透传 + 降级 | `dashboard/src/components/FormControlRenderer.tsx`、`dashboard/src/api.ts` | 前端 build |
| **M9-4 设计器接入** | FormDesigner 控件选项 / 参数编辑 / 级联层级维护 / 预览 | `dashboard/src/pages/workspace/FormDesigner.tsx`、locales | 前端 build |
| **M9-5 列表与筛选回显** | CrudListPage 码→文回显 + ref_labels + 筛选下拉 + ValueLabel 组件 | `dashboard/src/pages/workspace/CrudListPage.tsx`、`dashboard/src/components/ValueLabel.tsx`（新） | 前端 build + 离线 pytest |
| **M9-6 页面布局 schema 与接口** | layout_json / ui_config_json 读写 + 校验 + stat 聚合接口 | `src/datamason/workspace/`（新服务/路由）、`src/datamason/crud/engine.py`（stat）、`dashboard/src/api.ts` | 离线 pytest |
| **M9-7 画布设计器** | 引入 craft.js + react-grid-layout；LayoutDesigner 拖拽/配置/保存 | `dashboard/src/pages/workspace/LayoutDesigner.tsx`（新）、`dashboard/package.json`、`dashboard/src/api.ts` | 前端 build |
| **M9-8 运行态渲染与公开页** | PageLayoutRenderer + 列显隐/默认视图/详情/统计卡 + 公开页安全子集 + 降级 | `dashboard/src/components/PageLayoutRenderer.tsx`（新）、`dashboard/src/pages/workspace/CrudListPage.tsx`、公开页 | 前端 build + 离线 pytest |
| **M9-9 版本验收** | 真浏览器 E2E 剧本 + 独立验收报告 | `scripts/e2e/browser_e2e_m9.cjs`（新）、`docs/验收/` | 真浏览器 E2E + 全量 pytest + 前端 build |

**推荐顺序**：M9-0 → M9-1 → M9-2 → M9-3 → M9-4 → M9-5 → M9-6 → M9-7 → M9-8 → M9-9。M9-A/B（0~5）与 M9-C（6~8）可并行推进，但 C 依赖 A/B 的 `ValueLabel` 与 runtimeConfig 回显成果，建议先完成 0~5 再进 6~8。

**数量级参考**：M9 是**中大**次版本——三主线，复用面大（配置读写、CRUD 写路径、FormDesigner、antd 组件均复用），主要增量在「5 类控件与参数的规格/渲染」「列表回显 + FK 批量解析」「craft.js + RGL 页面布局画布」三处。不依赖 LLM，离线可测性高。

---

## 二、任务详细

### M9-0 控件规格扩展：spec 与元数据库加列

> **依赖**：无（起始任务）
> **目标**：建立 5 类新控件、控件参数与层级候选项的规格，并为元数据库补列。
> **验收线**：`.\.venv\Scripts\python.exe -m pytest tests\test_m9_0_spec.py -q` 全绿。

- **M9-0-1** `src/datamason/forms/spec.py`：`CONTROL_TYPES` 追加 `("switch", "slider", "rate", "select", "cascader")`（15 类）；`CONTROL_COMPAT` 补 5 类映射（switch→BOOLEAN；slider→数值族；rate→数值族；select→同 radio；cascader→字符串族）；`default_control_type` 不变。
- **M9-0-2** `spec.py` 新增 `ControlConfigSpec`（min/max/step/max_stars/allow_half/expand_trigger + 交叉校验：min<max、step>0、星数 1~10）；`FieldConfigSpec` 增加 `control_config` 字段并按控件类型强校验（rate.allow_half=True 须列 DECIMAL；slider 对整数列 step 须正整数）。
- **M9-0-3** `spec.py`：`OptionItemSpec` 增加 `parent_value: str | None = None`；`FieldConfigSpec._validate` 追加父引用存在性、成环/自引用拒绝、同层 value 唯一。
- **M9-0-4** `src/datamason/models/__init__.py`：`FormFieldConfig` 加 `control_config_json`（Text, default "{}", nullable）；`FormOptionItem` 加 `parent_value`（String(100), nullable）；在 `_backfill_missing_columns` 补两列（幂等）。
- **M9-0-5** 编写 `tests/test_m9_0_spec.py`：相容矩阵正反例；参数非法组合拒绝；parent_value 悬空/成环/自引用拒绝、合法层级通过；老库升级幂等（建表两次 + 补列不报错）。

### M9-1 写校验扩展：新控件校验注入

> **依赖**：M9-0
> **目标**：5 类新控件在三条写路径（工作台/公开表单/API key）统一校验生效。
> **验收线**：`.\.venv\Scripts\python.exe -m pytest tests\test_m9_1_validator.py -q` 全绿。

- **M9-1-1** `src/datamason/forms/service.py` `_validate_value` 追加 switch/slider/rate/select/cascader 分支（校验规则与错误消息见技术方案 §四）。
- **M9-1-2** `build_validator` / `build_registry_validator` 读取并透传 `control_config`；`cascader` 校验须先组装层级树判断末级。
- **M9-1-3** 编写 `tests/test_m9_1_validator.py`：非法值（越界/错步长/超星数/整数列半星/未知 select 值/父节点提交）各拒绝；合法值通过；未配置参数走默认行为。

### M9-2 后端映射服务：配置回读与 FK 批量标签

> **依赖**：M9-0、M9-1
> **目标**：读配置回传控件参数与层级树；列表 rows 携带 FK 批量标签。
> **验收线**：`.\.venv\Scripts\python.exe -m pytest tests\test_m9_2_mapping.py -q` 全绿。

- **M9-2-1** `src/datamason/forms/service.py` `_assemble` / `get_form_config`：输出字段增加 `control_config` 与层级选项树（`options` 含 parent_value）。
- **M9-2-2** `src/datamason/workspace/service.py` 新增 `attach_ref_labels(registry, table_name, rows)` 纯函数：可见 FK 列收集去重 id → 按列 `IN` 批量查引用表 `_label_column` → 返回 `ref_labels` 映射（查不到不放入）。
- **M9-2-3** `src/datamason/workspace/routes.py` `app_list_rows`：`list_rows` 后调 `attach_ref_labels`，响应增加 `ref_labels`；`finally dispose` 既有链路不变。
- **M9-2-4** 编写 `tests/test_m9_2_mapping.py`：层级树组装正确；ref_labels 映射正确；断言查询次数 = 可见 FK 列数（mock 计数）；查不到 id 不抛错；link 模式只读无写入。

### M9-3 前端控件渲染：FormControlRenderer 扩展

> **依赖**：M9-0（类型契约）；M9-2（运行时配置字段）
> **目标**：5 类新控件在表单中渲染，参数生效，无配置/异常降级。
> **验收线**：`npm --prefix dashboard run build` 通过；真浏览器辅助验证。

- **M9-3-1** `dashboard/src/api.ts`：`FormControlType` 联合类型追加 5 项；`FormFieldConfig` 增加 `control_config`；`FormOptionItem` 增加 `parent_value`；`FormConfigResponse`/`FormConfigPayload` 同步。
- **M9-3-2** `dashboard/src/components/FormControlRenderer.tsx`：`CONTROL_LABELS` 追加（开关/滑块/评分/下拉选择/级联选择）；import 追加 antd `Switch`/`Slider`/`Rate`/`Select`/`Cascader`；新增渲染分支与参数透传；`compatibleControls`/`defaultControlType`/`renderColumnPreview` 同步。
- **M9-3-3** 层级选项组装：前端把带 parent_value 的 options 组装为 Cascader 树；选中值映射为末级 value。
- **M9-3-4** 降级：无 options 的 select/radio 禁用 Input（既有）；配置异常走 degraded 默认渲染。

### M9-4 设计器接入：FormDesigner 扩展

> **依赖**：M9-3
> **目标**：设计器可配置 5 类控件、参数与级联层级，实时预览。
> **验收线**：`npm --prefix dashboard run build` 通过。

- **M9-4-1** `FormDesigner` 控件下拉追加 5 项（按 `compatibleControls` 过滤）。
- **M9-4-2** slider 参数编辑（min/max/step）、rate 参数编辑（max_stars/allow_half）、cascader 层级维护（按层级缩进 + 指定父项，先支持 2~4 层）。
- **M9-4-3** 实时预览接入新控件（复用 `renderColumnPreview`）。
- **M9-4-4** i18n：新增文案入 `locales/{en,zh}.json` 且键对齐。

### M9-5 列表与筛选回显：码→文 + ref_labels + 筛选下拉

> **依赖**：M9-2（ref_labels 契约）、M9-3（类型）
> **目标**：列表/筛选/详情统一按 label 展示，兜底原值；选项字段筛选下拉。
> **验收线**：`npm --prefix dashboard run build` 通过；离线 pytest（若涉及后端返回格式）。

- **M9-5-1** `dashboard/src/components/ValueLabel.tsx`（新）：入参 value / controlType / options / refLabels，输出 label；cascader 拼完整路径；查不到兜底原值；null 显示 `—`。唯一转换点。
- **M9-5-2** `CrudListPage`：拉 `FormsApi.runtimeConfig`（失败静默降级）；列渲染替换 `String(v)` 为 `ValueLabel`；FK 列用 `ref_labels`。
- **M9-5-3** 筛选：静态选项字段（radio/select/cascader）改下拉（显示 label、提交 value）；FK 筛选维持现状（§八 Q3 默认）。
- **M9-5-4** 详情/编辑回显复用 `ValueLabel`（编辑表单 initialValue 不变）。

### M9-6 页面布局 schema 与接口

> **依赖**：M9-2（运行态只读配置模式）；M9-5（回显组件）
> **目标**：布局 JSON 读写/校验 + stat 聚合接口，`"{}"` 走默认派生。
> **验收线**：`.\.venv\Scripts\python.exe -m pytest tests\test_m9_6_layout_api.py -q` 全绿。

- **M9-6-1** 新建 `src/datamason/workspace/layout.py`（纯领域）：布局 schema 定义与校验（区块类型有限集、引用表/列存在性、stat 聚合口径 count/sum/avg 且 sum/avg 目标列须数值列、RGL 坐标约束、归一化排序去重）。
- **M9-6-2** `src/datamason/workspace/service.py` 新增 `get_page_layout` / `save_page_layout` / `reset_page_layout` / `get_table_ui_config` / `save_table_ui_config`；写 `action="ui_config"` 审计；`"{}"` 语义。
- **M9-6-3** `src/datamason/workspace/routes.py` 新增 §6.3 六个接口；权限沿用 `_require_form_config` 语义；运行态只读接口复用 `_require_member_read` 语义。
- **M9-6-4** `src/datamason/crud/engine.py` 新增 `get_stat(registry, name, aggregation, column, filter)`（只读 SELECT + `_filtered` + 归属过滤；count 无需列；sum/avg 校验数值列）；workspace 暴露 `GET /app/<id>/api/tables/<name>/stat`。
- **M9-6-5** 编写 `tests/test_m9_6_layout_api.py`：读写/校验/非法拒绝/`"{}"` 默认/还原清空/stat 聚合与归属过滤/link 只读。

### M9-7 画布设计器：LayoutDesigner（craft.js + react-grid-layout）

> **依赖**：M9-6（接口）；M9-5（区块内字段配置复用）
> **目标**：物料面板拖区块、RGL 自由摆放、区块内字段配置、实时预览、保存/还原。
> **验收线**：`npm --prefix dashboard run build` 通过（记录体积增量）。

- **M9-7-1** `dashboard/package.json` 引入 `@craftjs/core` + `react-grid-layout`（MIT，DR-52）；`dashboard/src/api.ts` 新增布局接口分组（LayoutApi）。
- **M9-7-2** `dashboard/src/pages/workspace/LayoutDesigner.tsx`（新）：物料面板（table/detail/stat/html）+ RGL 画布（x/y/w/h 栅格 + 压实）+ craft.js 节点树（区块内字段/属性编排）。
- **M9-7-3** 区块属性面板：table 区块列显隐/列序/列宽/操作按钮/默认视图；detail 分组/列数；stat 聚合口径/标题；html 内容文本。
- **M9-7-4** 实时预览（走 mock runtimeConfig / 真实数据）+ 保存（写 M9-6 接口）+「还原默认」。
- **M9-7-5** 入口：列表/详情/公开页操作区「布局」按钮；`kind=dashboard` 页直达。i18n 文案入 locales。

### M9-8 运行态渲染与公开页

> **依赖**：M9-6、M9-7
> **目标**：工作台/公开页按布局 schema 渲染；解析异常降级默认；公开页仅安全子集。
> **验收线**：`npm --prefix dashboard run build` 通过；离线 pytest（公开页子集校验）。

- **M9-8-1** `dashboard/src/components/PageLayoutRenderer.tsx`（新）：读运行态只读布局 → RGL GridLayout 定位区块 → table/detail/stat/html 渲染；`"{}"` 走默认；解析异常 `degraded` 降级默认渲染。
- **M9-8-2** `CrudListPage` 按 `ui_config_json.list` 渲染列显隐/列宽/操作按钮/默认视图（defaultView 读配置，手动切换即时生效）。
- **M9-8-3** stat 区块调 `stat` 接口渲染统计卡。
- **M9-8-4** 公开页：只开放隐藏列/列序/详情布局安全子集，不开放拖拽入口；五类控件与显文存码生效。

### M9-9 版本 E2E 验收

> **依赖**：M9-0 ~ M9-8
> **目标**：真浏览器剧本全过 + 独立验收报告；全量 pytest + 前端构建全绿。
> **验收线**：`scripts/e2e/browser_e2e_m9.cjs` 全 PASS；`.\.venv\Scripts\python.exe -m pytest` 全绿；`npm --prefix dashboard run build` 通过。

- **M9-9-1** `scripts/e2e/browser_e2e_m9.cjs`（新）：剧本覆盖——配 5 类控件（含滑块范围步长/Rate 星数/三级级联）→ 录数存码 → 查库验证编码 → 列表/筛选显中文与完整路径 → FK 显名称 → 画布拖 table+stat+html → 保存 → 运行态渲染与 stat 数值 → 默认卡片视图 → 公开表单控件/显文存码生效 → 直连只读。
- **M9-9-2** 运行并修到 ALL PASS，删除调试日志。
- **M9-9-3** 产出 `docs/验收/2026xxxx-0.15.0-项目验收报告.md`。

---

## 三、验收清单（版本级）

- [ ] 离线全量 `.\.venv\Scripts\python.exe -m pytest` 全绿（新增 `tests/test_m9_*.py`）
- [ ] `npm --prefix dashboard run build`（tsc + vite）通过，记录 craft.js/RGL 体积增量
- [ ] `scripts/e2e/browser_e2e_m9.cjs` 全 PASS
- [ ] 业务库零 DDL；直连模式全程只读
- [ ] 零配置页面行为与 0.14 一致（回归）
- [ ] DR-46~53 已回写产品一页纸 §九·七（已完成）

---

*版本：0.15.0 开发计划 v1 ｜ 视角：任务拆分（When / Who）｜ 裁决：DR-46~53（已回写）｜ 配套：同目录《版本规划》《技术方案与实现闭环》*
