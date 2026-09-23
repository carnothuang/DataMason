# DataMason 0.13.0 开发计划 — 编码任务规划（M7）

> **需求来源**：`docs/规划/0.13.0/DataMason-0.13.0-需求规格.md`（等价主件 `.codeartsdoer/specs/form_controls_ui/spec.md`）。
> **技术方案**：`docs/规划/0.13.0/DataMason-0.13.0-技术方案与实现闭环.md`（等价主件 `.codeartsdoer/specs/form_controls_ui/design.md`）。
> **基线**：0.12.0（M6 混合建库已交付）；本计划对应里程碑 **M7（0.13.0）**。
> **视角**：任务拆分（When / Who）。需求（What）见需求规格；技术方案（How）见技术方案与实现闭环。
> **交付线**：离线 `.\.venv\Scripts\python.exe -m pytest` 全绿 + `npm --prefix web run build` 通过。
> **工程纪律**：不新增前端运行时依赖（复用 antd 既有 `Radio.Group` / `Checkbox.Group` / `DatePicker`）；不引入 alembic（沿用 `create_all_lazy` + 列回填）；LLM 只产 JSON 中间规格；业务库**零 DDL**；每个任务一次 commit（信息含任务号），全绿再进下一项。

---

## 一、任务总览

| 任务 | 内容 | 主要落点 | 验收方式 |
|------|------|---------|---------|
| **M7-0 地基** | 元数据库三表 + 控件类型/相容矩阵/配置载荷规格 | `server/models/__init__.py`、`server/forms/spec.py`（新） | 离线 pytest |
| **M7-1 配置服务** | 配置读 / 存 / 重置（蓝图求交、降级、单事务、审计） | `server/forms/service.py`（新） | 离线 pytest |
| **M7-2 写前校验** | `CrudRegistry.value_validator` 回调 + 三条写路径注入 | `server/crud/engine.py`、`server/crud/runtime.py`、`server/public/service.py` | 离线 pytest |
| **M7-3 配置路由** | 配置读写 / 运行态只读 / 权限判定 / 蓝图注册 | `server/forms/routes.py`（新）、`server/app.py` | 离线 pytest + HTTP 点验 |
| **M7-4 导出与公开页** | 配置包导出三节 + 公开页元数据向后兼容扩展 | `server/deploy/export.py`、`server/public/service.py` | 离线 pytest |
| **M7-5 前端渲染层** | `FormsApi` + 共用 `FormControlRenderer` + 工作台/公开表单配置驱动 | `web/src/api.ts`、`web/src/components/FormControlRenderer.tsx`（新）、`web/src/pages/workspace/CrudForm.tsx`、`web/src/pages/PublicPage.tsx` | 前端 build + 离线 pytest |
| **M7-6 前端配置器** | `FormDesigner` 编辑器 + 工程室页面卡「调整」入口 | `web/src/pages/workspace/FormDesigner.tsx`（新）、`web/src/pages/workspace/EngineerMenusPage.tsx` | 前端 build |
| **M7-7 候选项生成** | 自然语言生成候选项清单（可选能力，只产 JSON 不落库） | `server/llm/structure.py`、`server/forms/service.py`、`server/forms/routes.py`、`web/src/pages/workspace/FormDesigner.tsx` | 离线 pytest |
| **M7-8 版本验收** | 真浏览器 E2E 剧本 + 独立验收报告 | `scripts/e2e/browser_e2e_m7.cjs`（新）、`docs/验收/` | 真浏览器 E2E + 全量 pytest + 前端 build |

**推荐顺序**：M7-0（地基）→ M7-1（配置服务）→ M7-2（写前校验）→ M7-3（配置路由）→ M7-4（导出/公开页）→ M7-5（前端渲染层）→ M7-6（前端配置器）→ M7-7（候选项生成）→ M7-8（E2E 验收）。

**数量级参考**：M7 是**中等**次版本——单轨、复用面大（附件能力、CRUD 写路径、工程室页面卡、antd 组件均复用），主要增量在「三张配置表的读存链路」「配置驱动的渲染器」与「表单编辑器」。

---

## 二、任务详细

### M7-0 地基：元数据库三表与控件相容规格

> **依赖**：无（起始任务）
> **目标**：建立配置持久化结构与控件相容性规格，为后续全部任务提供事实基础。
> **验收线**：`.\.venv\Scripts\python.exe -m pytest tests\test_m7_0_forms_spec.py -q` 全绿。

- [x] **M7-0-1** 在 `server/models/__init__.py` 新增 `FormFieldConfig` 模型（表 `form_field_configs`）：`deployment_id`(Integer, FK `deployments.id`, index)、`table_name`(String(128))、`field_name`(String(128))、`control_type`(String(32), nullable)、`time_granularity`(String(16), nullable)、`readonly`(Boolean, default False)、`hidden`(Boolean, default False)、`hint`(String(200), default "")、`updated_by`(Integer)、`updated_at`(DateTime)；唯一约束 `(deployment_id, table_name, field_name)`。落点：`server/models/__init__.py`。
- [x] **M7-0-2** 新增 `FormOptionItem` 模型（表 `form_option_items`）：`deployment_id`、`table_name`、`field_name`、`value`(String(100))、`label`(String(100))、`order_index`(Integer)；唯一约束 `(deployment_id, table_name, field_name, value)`。落点：`server/models/__init__.py`。
- [x] **M7-0-3** 新增 `FormLayout` 模型（表 `form_layouts`）：`deployment_id`、`table_name`、`page_kind`(String(32))、`columns_per_row`(Integer, default 1)、`order_json`(Text)、`groups_json`(Text)、`updated_by`、`updated_at`；唯一约束 `(deployment_id, table_name, page_kind)`。落点：`server/models/__init__.py`。
- [x] **M7-0-4** 确认三表随 `create_all_lazy(checkfirst=True)` 自动建表且幂等（新表无存量数据，**不写列回填逻辑**）；确认部署销毁链路对三表级联清理。落点：`server/models/__init__.py`、`server/deploy/service.py`。
- [x] **M7-0-5** 新增 `server/forms/__init__.py` 与 `server/forms/spec.py`：`CONTROL_TYPES = ("text","textarea","number","boolean","radio","checkbox","date","datetime","ref","file")`（10 种）；`CONTROL_COMPAT` 相容矩阵（`file`→FILE；`ref`→存在外键的字段；`date`/`datetime`→DATETIME/VARCHAR；`radio`/`checkbox`→INTEGER/BIGINT/DECIMAL/VARCHAR/TEXT/BOOLEAN；`number`→INTEGER/BIGINT/DECIMAL；`text`/`textarea`→字符串族；`boolean`→BOOLEAN）；`assert_control_compatible(data_type, control_type, *, is_fk)`。落点：`server/forms/spec.py`（新）。
- [x] **M7-0-6** 在 `spec.py` 定义 pydantic 2 规格 `FieldConfigSpec` / `OptionItemSpec` / `LayoutSpec`（含 `OrderEntrySpec` / `GroupSpec`）/ `FormConfigPayload`，承载全部约束：候选项存储值/展示文案 ≤ 100 字符、提示文案 ≤ 200 字符、单字段候选项 ≤ 200、分组数 ≤ 20、分组名 ≤ 50 字符、`columns_per_row ∈ {1,2,3}`、控件类型 ∈ 白名单、候选项存储值同字段内唯一且非空。落点：`server/forms/spec.py`（新）。
- [x] **M7-0-7** 编写 `tests/test_m7_0_forms_spec.py`：建表幂等（重复 `create_all_lazy` 不报错）、三表唯一约束生效、相容矩阵正反例（如数值字段拒绝 `file`、`FILE` 字段接受 `file`）、规格边界拒绝（重复存储值 / 空存储值 / 候选超 200 / 非法列数 4 / 非法控件类型 / 提示超 200）。落点：`tests/test_m7_0_forms_spec.py`（新）。

### M7-1 配置服务：读 / 存 / 重置（含降级与审计）

> **依赖**：M7-0
> **目标**：实现配置的完整读写链路，覆盖蓝图求交、缺省补齐、原子保存、降级与审计。
> **验收线**：`.\.venv\Scripts\python.exe -m pytest tests\test_m7_1_service.py -q` 全绿。

- [x] **M7-1-1** 在 `server/forms/service.py` 实现 `get_form_config(app, deployment_id, table_name, *, page_kind)`：读三表 → 与 `table_specs.columns_json` 字段蓝图**求交**（丢弃已删除字段的残留配置）→ 未配置字段按默认映射补齐 → 返回 `{fields, options, layout, degraded}`；任一步异常整体降级为默认（`degraded=True`）并记录含表名与字段名的可检索错误。落点：`server/forms/service.py`（新）。
- [x] **M7-1-2** 实现 `save_form_config(app, deployment_id, table_name, payload, *, page_kind, actor_user_id)`：字段存在性校验（对照字段蓝图）→ 排除主键与归属列 → 相容性校验（`assert_control_compatible`）→ 候选项唯一/非空/数量/长度校验 → **单事务删旧写新三表** → 写 `action="form_config"` 审计（操作人/时间/表/字段/变更摘要）→ 返回保存摘要；任一环节失败整体回滚，原配置保持有效。落点：`server/forms/service.py`（新）。
- [x] **M7-1-3** 实现 `reset_field_config(app, deployment_id, table_name, field_name, *, actor_user_id)`：删除该字段的配置行与候选项行，写 `form_config` 审计（摘要含"恢复默认"）；字段已是最后一个自定义字段且布局为空时整表回到"未配置"状态。落点：`server/forms/service.py`（新）。
- [x] **M7-1-4** 定义模块自定义异常 `FormConfigError`（携带业务可读消息），供路由层映射 HTTP。落点：`server/forms/service.py`（新）。
- [x] **M7-1-5** 编写 `tests/test_m7_1_service.py`：保存后回读逐项一致、字段蓝图新增字段以默认控件出现且不改其他配置、删除字段后残留配置被安全忽略、配置缺失/损坏降级 `degraded=True` 且不报错、原子性（写过程中注入异常后旧配置保持、无部分生效）、审计记录落库、候选重复/超限拒绝、主键与归属列纳入被拒。落点：`tests/test_m7_1_service.py`（新）。

### M7-2 写前校验回调：三条写路径统一生效

> **依赖**：M7-0、M7-1
> **目标**：以依赖倒置回调让服务端校验在工作台、公开表单、API key 三条写路径统一生效，且 `server/crud/` 保持纯领域（不 import 元数据库）。
> **验收线**：`.\.venv\Scripts\python.exe -m pytest tests\test_m7_2_validator.py -q` 全绿。

- [x] **M7-2-1** 在 `server/forms/service.py` 实现 `build_validator(app, deployment_id, table_name, *, page_kind)` 返回 `Callable[[str, dict], None]`：闭包内一次读取该表配置，对提交载荷逐字段校验——单选值 ∈ 候选项、多选为数组且每项 ∈ 候选项且序列化长度 ≤ 字段长度、时间值可解析且粒度合法；违规抛 `ValueError`（业务可读消息）。落点：`server/forms/service.py`（新）。
- [x] **M7-2-2** `server/crud/engine.py` 的 `CrudRegistry.__init__` 新增可选参数 `value_validator: Callable[[str, dict], None] | None = None`（默认 `None`，存量行为**零变化**）；在 `create_row` / `update_row` 构造 `values` 完成后、写库前调用回调。落点：`server/crud/engine.py`。
- [x] **M7-2-3** `server/crud/runtime.py` 的 `_registry` 与 `registry_for_service` 装配 `CrudRegistry` 时注入 `forms.service.build_validator`。落点：`server/crud/runtime.py`。
- [x] **M7-2-4** `server/public/service.py` 的 `submit` 处直接构造 `CrudRegistry` 时同步注入校验回调。落点：`server/public/service.py`。
- [x] **M7-2-5** 编写 `tests/test_m7_2_validator.py`：三条写路径（工作台 / 公开表单 / API key）非法载荷（非法单选值、多选传非数组、多选含非法项、多选序列化超长、无法解析的时间）均被拒绝并返回可读错误；合法载荷通过；未注入 `value_validator` 时行为与升级前完全一致。落点：`tests/test_m7_2_validator.py`（新）。

### M7-3 配置 HTTP 路由与权限判定

> **依赖**：M7-1、M7-2
> **目标**：以薄路由暴露配置读写与运行态只读能力，并落实"仅创建人与平台管理员可读写配置"。
> **验收线**：`.\.venv\Scripts\python.exe -m pytest tests\test_m7_3_routes.py -q` 全绿；可加 HTTP 点验脚本核对真实响应。

- [x] **M7-3-1** 新增 `server/forms/routes.py`：模块级 `bp = Blueprint(...)` + `FormConfigError` 的 `@bp.errorhandler` 映射 HTTP（400 / 403 / 404），错误体统一 `{"error": …}`。落点：`server/forms/routes.py`（新）。
- [x] **M7-3-2** 实现配置管理路由（薄路由：解析 → 取 `session["user_id"]` → 权限校验 → 调服务（`current_app` 为首参）→ `jsonify`）：`GET /api/v1/deployments/<id>/forms/<table>`、`PUT /api/v1/deployments/<id>/forms/<table>`、`DELETE /api/v1/deployments/<id>/forms/<table>/fields/<field>`。落点：`server/forms/routes.py`（新）。
- [x] **M7-3-3** 实现运行态只读路由 `GET /api/v1/app/<id>/api/forms/<table>`：任意成员可读（操作员需正确渲染表单）；复用 `member_service.assert_member` 与 `table_access_map`，操作员仅可读其被授权表配置（越权读他表返回 403）。落点：`server/forms/routes.py`（新）。
- [x] **M7-3-4** 新增 `_require_form_config` 权限判定：部署 owner/creator **或** 平台管理员（`users.is_platform_admin`）；**不修改**既有 `_require_creator`（避免影响其他工程室动作）。落点：`server/forms/routes.py`（新）。
- [x] **M7-3-5** `server/app.py` 注册 `forms.bp`（URL 前缀 `/api/v1`，路由内自带完整路径）；鉴权守卫白名单**不变**（配置接口需登录，公开表单沿用 `/api/v1/public/*` 匿名放行）。落点：`server/app.py`。
- [x] **M7-3-6** 编写 `tests/test_m7_3_routes.py`：权限矩阵（创建人/平台管理员可读写、操作员/访客被拒 403）、操作员越权读他表配置 403、字段不存在 400、不相容控件 400、候选项重复 400、只读接口在配置缺失时返回 200 且 `degraded=true`（不报错）。落点：`tests/test_m7_3_routes.py`（新）。

### M7-4 导出扩展与公开页元数据扩展

> **依赖**：M7-1
> **目标**：让界面配置随部署导出可复现；让公开表单按独立布局渲染且满足最小暴露。
> **验收线**：`.\.venv\Scripts\python.exe -m pytest tests\test_m7_4_export_public.py -q` 全绿。

- [x] **M7-4-1** `server/deploy/export.py` 的 `build_package` 新增三节（`form_field_configs` / `form_option_items` / `form_layouts`，仅导出有配置的表）+ `counts` 计数；序列化失败即报错中止（不得产出不完整产物）；`EXPORT_VERSION` 保持 1（新增可选节，旧消费者忽略）。落点：`server/deploy/export.py`。
- [x] **M7-4-2** `server/public/service.py` 的 `_field_columns` / `get_page` 扩展：下发公开布局与候选项（顶层可选 `layout`，每字段可选 `control_type` / `time_granularity` / `readonly` / `hint` / `options`），**仅含该公开页允许公开的字段**；公开布局与工作台布局分别存储（`form_layouts.page_kind` 区分）。落点：`server/public/service.py`。
- [x] **M7-4-3** 编写 `tests/test_m7_4_export_public.py`：导出产物含三节与 `counts`、无配置表不出现在导出中、序列化失败中止；公开页仅下发本页字段与候选项（最小暴露，不含他表信息）、公开配置缺省时响应与升级前完全一致。落点：`tests/test_m7_4_export_public.py`（新）。

### M7-5 前端 API 与共用渲染层（工作台 + 公开表单）

> **依赖**：M7-3、M7-4
> **目标**：以单一 `FormControlRenderer` 驱动工作台与公开表单渲染，实现单选/多选/时间/附件控件，并保证"零配置即零变化"。
> **验收线**：`npm --prefix web run build` 通过；配套后端契约由 pytest 覆盖。

- [x] **M7-5-1** `web/src/api.ts` 新增 `FormsApi` 分组 + 类型（`FormFieldConfig` / `FormOptionItem` / `FormLayout` / `FormConfigPayload` / `FormConfigResponse`）；`PublicPageMeta` 扩展可选 `layout` 与字段可选控件字段。落点：`web/src/api.ts`。
- [x] **M7-5-2** 新增 `web/src/components/FormControlRenderer.tsx`：从 `CrudForm` 抽出 `FkSelect` / `FileField`（**行为不变**），按字段配置渲染控件（`Input` / `Input.TextArea` / `InputNumber` / `Select` / `Radio.Group` / `Checkbox.Group` / `DatePicker` / `FkSelect` / `FileField`）；`control_type` 为空 → 走既有默认映射；`date` / `datetime` → `DatePicker`（`showTime` 由粒度决定）；候选项为空时降级为文本输入并提示。落点：`web/src/components/FormControlRenderer.tsx`（新）。
- [x] **M7-5-3** `web/src/pages/workspace/CrudForm.tsx` 改为配置驱动：打开时拉 `FormsApi.runtimeConfig`（失败则 `null` 走默认映射，降级不阻断）；引入栅格容器支持列数 / 分组 / 顺序 / 显隐 / 只读 / 提示；多选序列化长度校验挂 `Form.Item` rules；附件"移除"改为**仅移除字段值引用**（不调 `FilesApi.remove`，避免误删仍被引用的附件）。落点：`web/src/pages/workspace/CrudForm.tsx`。
- [x] **M7-5-4** `web/src/pages/PublicPage.tsx` 改为配置驱动：复用 `FormControlRenderer`，按公开布局渲染；配置缺省时与升级前完全一致。落点：`web/src/pages/PublicPage.tsx`。
- [x] **M7-5-5** 编写回归覆盖（后端渲染契约 + 手动核对）：`control_type=null` 时默认映射与升级前一致；多选回填完整（不多不少）；单选准确选中；日期时间回显同一时刻；附件名展示与失效占位。落点：`tests/`、手工核对记录。
- [x] **M7-5-6** 前端验证：`npm --prefix web run build` 通过（`tsc --noEmit && vite build`）。落点：`web/`。

### M7-6 前端表单配置器与工程室入口

> **依赖**：M7-5
> **目标**：让配置者在工程室内完成控件类型、候选项与布局调整，并能实时预览、草稿不泄漏。
> **验收线**：`npm --prefix web run build` 通过；交互在 M7-8 真浏览器剧本中验收。

- [x] **M7-6-1** 新增 `web/src/pages/workspace/FormDesigner.tsx`：字段列表（可拖拽排序）→ 分组管理 → 列数选择 → 逐字段控件类型下拉（**仅列出相容项**）→ 候选项表格（增删改排序，实时校验唯一 / 非空 / 上限 200）→ 显隐 / 只读开关 → 提示文案 → **实时预览区**（复用 `FormControlRenderer`，所见即所得）→ 保存；草稿仅浏览器内存维护，保存才落库。落点：`web/src/pages/workspace/FormDesigner.tsx`（新）。
- [x] **M7-6-2** 接入 `web/src/pages/workspace/EngineerMenusPage.tsx` 页面卡「调整」：扩展为打开 `FormDesigner`（标题调整保留在编辑器内一并提交）；**不改动**菜单树拖拽与保存菜单逻辑。落点：`web/src/pages/workspace/EngineerMenusPage.tsx`。
- [x] **M7-6-3** 处理配置器异常场景：不相容控件前端禁用并说明原因；隐藏必填字段时给出确认提示；配置引用已删除字段时提示"部分配置已失效"；布局配置损坏时降级提示。落点：`web/src/pages/workspace/FormDesigner.tsx`（新）。
- [x] **M7-6-4** 前端验证：`npm --prefix web run build` 通过。落点：`web/`。

### M7-7 候选项自然语言生成（可选能力）

> **依赖**：M7-3
> **目标**：支持以自然语言生成候选项候选清单，生成结果经配置者确认后才写入配置。
> **验收线**：`.\.venv\Scripts\python.exe -m pytest tests\test_m7_7_options.py -q` 全绿。

- [x] **M7-7-1** `server/llm/structure.py` 新增 `generate_option_items(client, *, field_label, description, max_items=200)`：产出候选项 JSON 中间规格 `(list[{value, label}], meta)`，失败回喂重试沿用 `MAX_RETRY=2`；**只产 JSON，不落库、不产配置**。落点：`server/llm/structure.py`。
- [x] **M7-7-2** `server/forms/service.py` 新增 `generate_option_items(app, *, field_label, description)`：调 LLM → 确定性去重 / 裁剪（≤ 200）→ `ai_service.record`（`kind="options"`）埋点（成功失败均记）→ **仅返回清单**。落点：`server/forms/service.py`（新）。
- [x] **M7-7-3** `server/forms/routes.py` 新增 `POST /api/v1/deployments/<id>/forms/<table>/fields/<field>/options/generate`（配置者）：未配置默认模型 400，LLM 超时 / 返回不合法结构 502，**保留原配置不变**。落点：`server/forms/routes.py`（新）。
- [x] **M7-7-4** `web/src/pages/workspace/FormDesigner.tsx` 接入生成入口：展示候选清单供配置者勾选，**确认后才随保存写入**（未确认不落库）。落点：`web/src/pages/workspace/FormDesigner.tsx`（新）。
- [x] **M7-7-5** 编写 `tests/test_m7_7_options.py`：LLM 桩产出清单、确定性去重与裁剪、埋点记录（成功/失败）、生成失败保留原配置、未确认不写入配置。落点：`tests/test_m7_7_options.py`（新）。

### M7-8 版本端到端验收

> **依赖**：M7-5、M7-6、M7-7
> **目标**：以真浏览器剧本贯通"配置 → 渲染 → 校验 → 回填"全链路，并产出独立验收报告。
> **验收线**：真浏览器 E2E 通过 + 全量 `pytest` 全绿 + `npm --prefix web run build` 通过。

- [x] **M7-8-1** 新增 `scripts/e2e/browser_e2e_m7.cjs` 真浏览器剧本：工程室打开表单编辑器 → 配置单选 / 多选 / 日期时间 / 附件字段与分组列数 → 预览 → 保存 → 工作台新增与编辑表单按配置渲染 → 绕过前端提交非法单选值被服务端拒绝 → 多选重新打开完整回填 → 公开表单仅暴露本页字段与候选项。落点：`scripts/e2e/browser_e2e_m7.cjs`（新）。
- [x] **M7-8-2** 运行全量离线测试与前端构建：`.\.venv\Scripts\python.exe -m pytest` 全绿 + `npm --prefix web run build` 通过。落点：仓库根。
- [x] **M7-8-3** 产出独立验收报告 `docs/验收/2026xxxx-0.13.0-项目验收报告.md`（沿用既有格式），记录 M7 验收线结果与未通过项跟进。落点：`docs/验收/`。

---

## 三、需求覆盖矩阵

| 需求条目 | 覆盖任务 |
|---------|---------|
| §4.1.1 表单首屏 ≤ 1.5s | M7-1-1（一次读配置）、M7-5-3（一次拉取） |
| §4.1.2 配置保存 ≤ 1s | M7-1-2（单事务） |
| §4.1.3 候选项上限 200 | M7-0-6（规格）、M7-6-1（前端实时校验） |
| §4.1.4 附件超限前置提示 | M7-5-2（`FileField.beforeUpload` 前置校验） |
| §4.2.1 配置一致性（原子） | M7-1-2（单事务删旧写新） |
| §4.2.2 数据不丢失 | M7-1-2、M7-5-3（仅改呈现） |
| §4.2.3 降级可用 | M7-1-1（`degraded`）、M7-5-3、M7-5-4 |
| §4.2.4 回填保真 | M7-5-2、M7-5-5 |
| §4.2.5 附件解绑语义 | M7-5-3（仅移除引用，不调 `FilesApi.remove`） |
| §4.3.1 配置权限 | M7-3-4（`_require_form_config`） |
| §4.3.2 公开表单最小暴露 | M7-4-2 |
| §4.3.3 候选项值服务端校验 | M7-2-1、M7-2-5 |
| §4.3.4 附件安全 | M7-5-2（复用既有白名单） |
| §4.3.5 审计留痕 | M7-1-2、M7-1-3 |
| §4.4.1 配置可导出 | M7-4-1 |
| §4.4.2 可观测 | M7-1-1、M7-7-2 |
| §4.4.3 可诊断（含表名+字段名） | M7-1-1 |
| §4.5.1 存量兼容（零配置零变化） | M7-5-3、M7-5-4、M7-2-2（默认 `None`） |
| §4.5.2 配置向后兼容（可缺省） | M7-0-6、M7-5-2 |
| §4.5.3 字段蓝图联动 | M7-1-1（读时求交） |
| §4.5.4 不改存储结构（零 DDL） | M7-0-4、M7-1-2 |
| §5.1.1 默认控件映射不变 | M7-5-2 |
| §5.1.1 单选 / 多选 / 时间 / 附件控件 | M7-5-2、M7-5-3、M7-5-5 |
| §5.1.1 必填与候选联合校验 | M7-2-1、M7-2-5 |
| §5.1.3 候选项为空降级 | M7-5-2 |
| §5.1.3 配置与蓝图不匹配 | M7-1-1 |
| §5.1.3 多选值超长 | M7-2-1 |
| §5.1.3 附件上传失败 / 引用失效 | M7-5-3、M7-5-5 |
| §5.1.3 候选项生成失败 | M7-7-3 |
| §5.1.3 并发修改冲突 | M7-1-2（后写覆盖 + 双次审计） |
| §5.2.1 可选控件类型 / 相容约束 | M7-0-5、M7-0-6、M7-6-1 |
| §5.2.1 候选值来源（增删改排序） | M7-1-2、M7-6-1 |
| §5.2.1 候选值来源（NL 可选） | M7-7-1 ~ M7-7-5 |
| §5.2.1 配置可回退 | M7-1-3 |
| §5.2.1 配置可预览 | M7-6-1（复用渲染器） |
| §5.2.1 禁止改类型 / 禁止越权 | M7-0-6、M7-3-4 |
| §5.3.1 顺序 / 分组 / 列数 / 显隐 / 只读 / 提示 | M7-0-6、M7-1-2、M7-5-3、M7-6-1 |
| §5.3.1 布局作用范围（工作台 + 公开单独可控） | M7-1-1（`page_kind`）、M7-4-2 |
| §5.3.1 禁止项（不删字段 / 不纳主键归属列） | M7-0-6、M7-1-2 |
| §5.3.3 隐藏必填 / 列数不匹配 / 布局损坏 | M7-6-3 |
| §5.4.1 持久化 / 解耦 / 生效时机 / 草稿不泄漏 | M7-1-2、M7-6-1 |
| §5.4.1 配置随部署导出 | M7-4-1 |
| §5.4.3 导出遗漏报错 / 读取越界 | M7-4-1、M7-3-3 |
| §6.1 ~ §6.4 数据约束 | M7-0-1 ~ M7-0-6 |

---

## 四、依赖关系图

```
M7-0 地基（模型 + 规格）
  │
  ├─► M7-1 配置服务（读/存/重置）
  │      │
  │      ├─► M7-2 写前校验回调 ──► M7-3 配置路由与权限
  │      │                              │
  │      │                              ├─► M7-7 候选项 NL 生成（可选）
  │      │                              │
  │      └─► M7-4 导出与公开页扩展 ─────┤
  │                                     ▼
  └────────────────────────────► M7-5 前端渲染层（FormsApi + Renderer + CrudForm/PublicPage）
                                        │
                                        ▼
                                  M7-6 前端配置器（FormDesigner + 工程室入口）
                                        │
                                        ▼
                                  M7-8 版本端到端验收
```

**并行性说明**：M7-4 与 M7-3 可并行（均仅依赖 M7-1）；M7-7 可在 M7-5 前后独立推进（仅依赖 M7-3 与 M7-5 的配置器入口）。M7-5 依赖 M7-3 与 M7-4 的接口契约稳定后再启动。

---

## 五、验收标准汇总

**离线（pytest，每任务验收线）**
- 地基：三表建表幂等、唯一约束生效；相容矩阵正反例正确；规格边界（重复/空值/超限/非法列数/非法控件类型/提示超长）拒绝。
- 配置服务：保存回读一致；蓝图求交丢弃残留；缺省降级不报错；单事务原子性；审计落库；主键归属列与非法候选项拒绝。
- 写前校验：三条写路径非法载荷统一 400 拒绝，合法载荷通过，未注入时行为不变。
- 配置路由：权限矩阵正确；越权读他表 403；错误映射（400/403/404）正确。
- 导出与公开页：导出含三节与 counts、序列化失败中止；公开页最小暴露、缺省兼容。
- 候选项生成：LLM 桩产出清单、去重裁剪、埋点记录、失败保留原配置、未确认不落库。

**前端（build，每前端任务验收线）**
- `npm --prefix web run build`（`tsc --noEmit && vite build`）通过。
- 复用 antd 既有组件，**无新增运行时依赖**。

**真实浏览器（Playwright，里程碑验收线）**
- 工程室配置单选 / 多选 / 日期时间 / 附件与分组列数 → 预览 → 保存 → 工作台表单按配置渲染；
- 绕过前端提交非法单选值 → 服务端拒绝；
- 多选重新打开完整回填（不多不少）；
- 公开表单仅暴露本页字段与候选项（红线：不泄漏他表信息）；
- 零配置表表单表现与升级前完全一致。

**版本级**：全量 `pytest` 全绿 + `npm --prefix web run build` 通过 + 独立验收报告（`docs/验收/`，沿用既有格式）。

---

*文档版本：0.13.0 开发计划 v1 ｜ 视角：任务拆分 ｜ 基线：0.12.0（M6 已交付）｜ 里程碑：M7 ｜ 归档：`.codeartsdoer/specs/form_controls_ui/tasks.md`、`docs/规划/0.13.0/DataMason-0.13.0-开发计划.md`*