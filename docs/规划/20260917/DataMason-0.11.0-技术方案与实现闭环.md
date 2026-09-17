# DataMason 0.11.0 技术方案与实现闭环

> 配套《DataMason-0.11.0-版本规划.md》。本文件按《产品一页纸》§九·四 维护规则第 6 条提供**实现闭环说明**：表间关联、接口契约、UX 步骤落库动作，确保一致性走查不遗漏可落地性。
> 基线代码：0.10.0（M0–M4）。所有引用均为仓库内真实文件；`file:line` 指本文件撰写时的位置，实施时可能漂移。

---

## 一、总原则与现状锚点

| 事实（0.10.0） | 位置 | 对 0.11.0 的含义 |
|---|---|---|
| 元数据表全部**惰性建表** + `_backfill_missing_columns` 简单列迁移 | `server/models/__init__.py:298-345` | 新增表/列不需要 alembic；改动走同一手法，保证老库升级 |
| 运行时表白名单**来自 `table_specs`** | `server/crud/runtime.py:38-44` | 增表=写 `table_specs` 行即暴露；删表=删行即隐藏（但仍须清页面/菜单，防悬垂） |
| CRUD 引擎权限旋钮只有 `current_user_id` + `owner_only` | `server/crud/engine.py:45-62`、`_owner_condition:204-211` | 操作权限与数据范围在此扩展 |
| 操作员表范围 = 菜单→页面→表 | `server/members/service.py:104-135` | 新表/新页面必须成对，否则操作员不可见 |
| 直连外部库只读红线 | `server/deploy/editor.py:111-117`、`deploy/service.py:294-299` | 结构演化只在创建模式放开 |
| 两阶段日志 + 启动对账 | `server/audit/service.py`、DR-03 | 结构/附件/导入的写动作都接同一套 |
| 全站未设 `MAX_CONTENT_LENGTH`，无 multipart 处理 | `server/app.py`、`server/config.py` | 文件能力需显式补齐并设上限 |
| `crud_configs.ui_config_json` 恒为 `{}`、运行时从不读 | `server/deploy/service.py:382-387` | 用作字段级权限的落点（V0.1 可选） |
| 配置包导出只含元数据+committed 日志，不含业务数据/附件 | `server/deploy/export.py` | 附件与业务数据另走文件/表格导出 |

**三条不可破的约束**：① 绝不 ALTER 外部库；② 生成即提案（不确认不落库）；③ 业务写必留痕（两阶段 + 对账），批量的留痕语义见 DR-29。

---

## 二、元数据模型变更

### 2.1 新增表 `attachments`（DR-28）

```
attachments
├─ id            PK
├─ deployment_id FK deployments.id
├─ table_name    VARCHAR(128)      # 业务表名（非 table_specs.id，便于行删除时不强依赖）
├─ row_pk        VARCHAR(128)      # 所属行业务主键（字符串化）
├─ field_name    VARCHAR(128)      # 附件字段名
├─ token         VARCHAR(64) UNIQUE# 随机 token，文件名=token
├─ filename      VARCHAR(255)      # 原始文件名（仅展示/下载命名用）
├─ mime          VARCHAR(128)
├─ size          BIGINT
├─ storage_rel   VARCHAR(512)      # 相对 DATA_ROOT 的路径（deployments/<stem>.files/<token>.<ext>）
├─ status        VARCHAR(16)       # active / pending_gc（游离待回收）
├─ uploaded_by   BIGINT            # users.id
└─ created_at    DATETIME
```

- **为什么不用 `table_id`**：行删除时按 `(deployment_id, table_name, row_pk, field_name)` 定位附件最直接，不依赖 `table_specs.id` 的生命周期。
- **多附件**：`field_name` 多行即多附件；业务表该列存 `[attachment_id,...]`（JSON 串）。单文件列存单个 `attachment_id`。
- 惰性建表（`Base.metadata.create_all`）自动建；无需回填。

### 2.2 `deployment_menu_grants` 扩展（DR-30）

```
deployment_menu_grants            # 现有：复合 PK(menu_id, user_id)
├─ menu_id  PK/FK
├─ user_id  PK/FK
├─ actions_json  TEXT default '{}'   # 新增；空/{} = 按角色默认
│    # {"view":true,"create":true,"update":true,"delete":false,"export":false,"import":false}
└─ data_scope    VARCHAR(8) default ''  # 新增；''=角色默认, 'all', 'own'
```

- 用 `_backfill_missing_columns` 的 `ADD COLUMN ... DEFAULT` 手法；SQLite/MySQL/PG 对复合主键表加列均可行。
- **空值语义**：`actions_json='{}'` 且 `data_scope=''` = 按角色默认（创建人/管理员全动作+all；操作员当前实际能力+own），保证升级不改变存量行为。

### 2.3 `ColumnType` 增 `FILE`（DR-28）

- `server/ddl/table_spec.py:14` 的 Literal 增加 `"FILE"`；`ColumnSpec.type` 支持。
- `server/ddl/ddl_builder.py` `_map_type`：`FILE → VARCHAR(255)`（三方言一致，存 token / token 数组串）；`_column_ddl` 无需特殊处理。
- `server/deploy/link.py:282-283` 的外部二进制映射维持降级为 `TEXT`（外部库不引入 FILE 语义；FILE 仅创建模式新表使用）。
- `server/deploy/editor.py` `_logical_type`：DB 侧仍是 VARCHAR，回读为 VARCHAR，**FILE 语义由 `columns_json` 中的逻辑类型保留**（`get_blueprint` 读的是 `columns_json`，所以能保住）。
- `server/crud/engine.py._coerce_value`：`FILE` 按字符串处理（存 token），不参与数值/日期转换。
- `server/workspace/routes.py.app_meta` 需要把 `is_attachment` 或 `type=FILE` 透传给前端（当前 app_meta 已用 `table_specs.columns_json` 覆盖类型，天然可带出）。

### 2.4 配置项新增

- `Config.max_content_length`（如 20MB）→ Flask `MAX_CONTENT_LENGTH`；单文件上限另设（如 10MB），在文件接口内二次校验。
- `Config.upload_allowed_ext` / `upload_allowed_mime`：白名单。
- `requirements.txt` 增 `openpyxl>=3.1`（导入导出 xlsx）。

---

## 三、结构演化实现闭环（M5-A）

### 3.1 增表：UX 步骤 → 落库动作

```
[工程室·数据蓝图] 点「+ 新建表」
  └─ 选入口：表单式 / 一句话式
       ├─ 表单式：填表名/业务名/字段（复用 FieldEditor 的字段编辑交互）
       └─ 一句话式：LLM NL→ table_spec（单表），走既有 llm 网关
  → ① 提案（只算不执行）
       服务端校验：
         - link 模式 → 400（_assert_editable）
         - 表名重名（查 table_specs + 真实库 reflect）
         - 单主键、字段名合法、无归属列占用（ColumnSpec/TableSpecModel 既有校验）
         - 外键 ref_table 必须已存在且 ref_column 是真实主键（映射到该部署已落库表）
         - 归属列注入（inject_ownership_columns）
         - 生成 DDL（build_table_ddl）+ 将生成的 page_spec(menu/crud_config) 计划
      返回提案卡数据：{table, columns, fk_relations, statements, pages}
  → ② 确认执行
       服务端（单事务内按序）：
         a. execute_ddl(create table [+indexes])
         b. 回读 inspect() 生成 rich columns → 写 table_specs(row)
         c. 写 page_specs(kind=crud_list, access=member, table_id)
         d. 写 deployment_menus(kind=table, ref_id=page_spec.id, order_index 追加)  # 默认不授权
         e. 写 crud_configs(api_config_json="{}", ui_config_json="{}")
         f. audit.log_system(action=structure, object_kind=table, object_label="新增表：X")
      返回：{table, columns, page_id, menu_id, statements}
```

**关键点**
- 外键排序：单表新增时引用方是**本表**，被引用表已存在，无须拓扑排序；但若一句话式产出了多表（LLM 自由发挥），**必须限制为单表**或对新增子集做拓扑排序（v0 明确只允许单表，减少风险）。
- 与首次部署 `confirm`（`server/deploy/service.py:340-388`）保持**同一套产物结构**，避免两条路径分叉。
- 停止态允许；销毁态 404/409。

### 3.2 删表：影响面计算 → 守卫 → 级联

**影响面（只算）**：
```
行数：SELECT COUNT(*) FROM <table>
外键引用：遍历该部署 table_specs 的 columns_json，找 is_fk 且 ref_table==<table> 的列
          （并回真实库确认约束确实存在）
页面/菜单/授权：page_specs WHERE table_id=... → menus(ref_id in page_ids)
               → deployment_menu_grants(menu_id in menu_ids)
流程：flow_specs.steps[].page_id ∈ 上述 page_ids
报表：page_specs(kind=dashboard).layout.sql 是否引用该表（字符串/解析判断，保守起见关键字匹配）
种子：deployment_seed_rows WHERE table_name=...
```

**守卫（任一命中即拒绝，返回可读原因）**：
1. 被其他业务表外键引用 → 拒绝（提示引用表/列）。
2. 被流程步骤引用 → 拒绝（提示流程名）。
3. 非创建人 → 403；link 模式 → 400；销毁态 → 409。

**确认执行（级联顺序，先子后父，参照 `destroy_deployment`）**：
```
1. deployment_menu_grants (menu_id in 该表菜单)
2. deployment_menus
3. page_specs (含 dashboard/流程页中该表的)
4. crud_configs (table_id)
5. deployment_seed_rows (table_name)
6. attachments (table_name, 标记 pending_gc 后回收文件)
7. table_specs (该行)
8. DROP TABLE <table>          # 创建模式，真实库
9. audit.log_system(action=structure, object_label="删除表：X（N 行，M 个页面）")
```

> 顺序说明：先清元数据再 DROP 物理表，即使 DROP 失败也只会留一张"无界面"的孤儿表（可再删/可重建），不会留下悬垂元数据。DROP 失败需回滚前面的元数据删除（同一事务提交元数据；物理 DDL 在最后，失败则显式补偿——沿用首次部署"DDL 事务语义"的既有处理方式）。

### 3.3 接口契约（创建人独有）

| 方法 | 路径 | 作用 | 请求 | 返回 |
|---|---|---|---|---|
| POST | `/api/v1/deployments/{id}/tables/propose` | 增表提案 | `{mode:"form"|"nl", spec?:TableSpec, description?:str}` | `{table, columns, relations, statements, pages, valid, errors}` |
| POST | `/api/v1/deployments/{id}/tables/apply` | 增表执行 | `{spec: TableSpec}`（服务端重算，不信任提案原文） | `{table, page_id, menu_id, statements}` |
| POST | `/api/v1/deployments/{id}/tables/{name}/drop/propose` | 删表影响面 | — | `{table, row_count, referenced_by[], pages[], menus[], flows[], reports[], seeds, blocked, reason}` |
| POST | `/api/v1/deployments/{id}/tables/{name}/drop` | 删表执行 | `{confirm: <表名>}` | `{dropped, cascade:{pages,menus,grants,configs,seeds}}` |

- 全部 `_require_creator`（`server/deploy/routes.py:436-444`）+ `_assert_editable`。
- 提案与执行使用**同一构建函数**，pytest 断言"提案语句 == 执行语句"（沿用 M2-8 的测试手法 `tests/test_m2_8_blueprint.py`）。

---

## 四、附件与文件服务实现闭环（M5-B 文件）

### 4.1 存储布局与隔离

```
DATA_ROOT/
└─ deployments/
   ├─ <stem>.db                 # 该部署业务库（创建模式）
   └─ <stem>.files/             # 新增：该部署附件目录
      └─ <token>.<ext>
```
- `<stem>` = `deployments.db_name` 去掉 `.db` 后缀（`server/deploy/service.py:252-262` 生成的唯一名）。
- 隔离经**每部署目录** + **每请求部署校验**双重保证：文件接口先从 `attachments` 校验 token 属于该 `deployment_id`。

### 4.2 接口契约

| 方法 | 路径 | 说明 | 鉴权 |
|---|---|---|---|
| POST | `/api/v1/app/{id}/api/files` | multipart 上传；表单字段 `file`，可选 `table/row_pk/field` | 成员；动作权限 `create/update` 由调用方上下文决定（v0 仅要求成员 + 数据范围校验） |
| GET | `/api/v1/app/{id}/api/files/{token}` | 下载（`Content-Disposition: attachment`） | 成员 + 数据范围（操作员只能取自己行的附件） |
| GET | `/api/v1/app/{id}/api/files/{token}/preview` | 内嵌预览（仅图片/PDF/文本） | 同上；其余类型 415 |
| DELETE | `/api/v1/app/{id}/api/files/{token}` | 删除（同时从所属行字段移除引用） | 成员 + 行写权限 |
| GET | `/api/v1/app/{id}/api/files/quota` | 部署级用量（可选） | 创建人/管理员 |

- **路径前缀不在 API key 白名单内**（`server/app.py:152-157` 只放行 `/app/{id}/api/tables/` 与 `/api/meta`），故 key 通道默认无法访问文件接口——符合 DR-33。
- **鉴权顺序**：全局 guard（登录）→ `assert_member` → 若操作员，按 `(deployment_id, table_name, row_pk)` 回查该行 `created_by` 是否等于自己（数据范围）→ 通过。

### 4.3 安全清单（DR-33）

1. token = `secrets.token_urlsafe`（≥32 字节），文件名即 token，**不落原始文件名到磁盘**（`filename` 只存元数据表，下载时用于 `Content-Disposition`）。
2. 扩展名白名单 + MIME 探测；未知/危险类型拒绝（可执行、脚本、HTML 默认禁止或强制 attachment）。
3. 单文件 ≤ 10MB、单请求 ≤ `MAX_CONTENT_LENGTH`；超限 413。
4. 路径穿越：`storage_rel` 一律由服务端拼接并 `resolve()`，校验仍在 `DATA_ROOT` 内（复用 `destroy_deployment` 的守卫写法 `server/deploy/service.py:619,627-634`）。
5. 预览响应加 `X-Content-Type-Options: nosniff`，禁止把用户文件当同源 HTML 执行。

### 4.4 生命周期闭环

- **创建**：上传 → 写 `attachments` → 返回 token → 前端把 token 写进所属行的 FILE 字段（行新增/修改走既有 CRUD 写路径，于是**附件与行的关联也进 `activity_logs`** 的字段级 `changes`）。
- **删除行**：CRUD delete 钩子里，按 `(deployment_id, table_name, row_pk)` 把附件置 `pending_gc`；后台/启动对账任务回收文件与行。
- **替换字段**：update 时旧 token 变新 token，旧附件置 `pending_gc`。
- **销毁部署**：`destroy_deployment` 级联加一步"删除 `<stem>.files/` 目录"（路径守卫同上）。
- **游离回收**：启动任务扫描 `pending_gc` 与"元数据行已不存在"的附件，删除磁盘文件并删元数据行（失败可重入，幂等）。

### 4.5 前端

- `CrudForm.tsx` `renderWidget` 增加 `FILE` 分支：单文件上传（antd `Upload`）或多附件（`fileList` 模式）；提交前先上传拿 token，再写行。
- `CrudListPage.tsx` 单元格：FILE 列渲染 `FileChip`（文件名 + 大小 + 预览/下载/删除）。
- 复用 `demo.ts` 的脱敏：演示模式下 FILE 列只显示"附件 · 已脱敏"，不触发下载。

---

## 五、导入导出实现闭环（M5-B 批量）

### 5.1 依赖与解析

- CSV：标准库 `csv`（UTF-8 BOM 兼容、可选 GBK 探测）。
- XLSX：`openpyxl`（只读 `read_only=True` 流式；写出用 `Workbook`）。
- 上传文件落 `DATA_ROOT/tmp/import/<token>.*`，token 有 TTL，定期清理；不进入 `attachments`（导入文件是过程物）。

### 5.2 接口契约

| 方法 | 路径 | 作用 |
|---|---|---|
| POST | `/api/v1/app/{id}/api/tables/{name}/import/preview` | 上传并解析：返回表头、前 N 行、自动列映射、逐行校验结果摘要 |
| POST | `/api/v1/app/{id}/api/tables/{name}/import/commit` | 按确认的映射执行导入：`{upload_token, mapping, mode:"strict"|"skip_errors"}` |
| GET | `/api/v1/app/{id}/api/tables/{name}/import/template` | 下载模板（按列生成表头 + 示例行） |
| GET | `/api/v1/app/{id}/api/tables/{name}/import/errors/{token}` | 下载错误清单 |
| GET | `/api/v1/app/{id}/api/tables/{name}/rows.xlsx` | XLSX 导出（与 `rows.csv` 同筛选参数） |

### 5.3 校验与执行

- **列映射**：按 `ColumnSpec.name` 与 `biz_name` 双匹配；未匹配列默认忽略；必填列未映射 → 阻断。
- **逐行校验**：必填（`nullable=False`）、类型可转换（复用 `_coerce_value`）、VARCHAR 长度、唯一（`unique` 列：文件内重复 + 查库重复）、外键存在（复用 `list_refs` 的目标表查询）。
- **执行**：`strict` = 全部行在一个事务内写入，任一失败整体回滚（错误清单先返回，不写）；`skip_errors` = 只写通过校验的行，分批提交（如每 500 行），返回成功/跳过计数与错误清单。
- **日志（DR-29）**：写 **1 条** `activity_logs(action=import, object_kind=table, object_label="导入 X：成功 N 行，跳过 M 行（report.xlsx）", status=committed)`。两阶段写入沿用；不逐行写。
- **权限**：导入需 `import` 动作权限；导出需 `export` 动作权限（§六）。

### 5.4 导出增强

- `rows.xlsx` 复用 `export_rows`（`server/crud/engine.py:445-469`）的筛选逻辑，改为 openpyxl 写出；列选择参数 `cols=a,b,c`。
- 上限：从硬编码 5000 改为配置项；超限时明确返回"已导出前 N 行"或 400 提示分批。
- 权限与数据范围在取数前统一校验（操作员导出=自己行）。

---

## 六、权限模型实现闭环（M5-C）

### 6.1 统一权限解析器（新增 `server/members/` 内纯函数）

```
resolve_access(sess, deployment, user_id, table_name, page_id=None)
  → { role, actions:set[str], data_scope:"all"|"own", writable:bool }
```
- 创建人/管理员：`actions = 全部`，`data_scope="all"`（沿用 DR-14 的边界，不含工程/停止/销毁/分享——那是部署级动作，不走本解析器）。
- 操作员：收集该用户所有 grants（`menu_id ∈ 其授权`），找到 `menu.ref_id → page_spec.table_id == table_name` 的功能；`actions` = 各功能 `actions_json` 的并集（空=角色默认=当前实际能力）；`data_scope` = **取最宽**（任一功能 all 则 all）。
- **无归属列**：`data_scope="own"` 但表无 `created_by` → 降级为可见全部并**明示**（沿用 DR-05，绝不 ALTER）。

### 6.2 强制点

| 动作 | 强制位置 |
|---|---|
| view（列表/详情/refs） | `server/workspace/routes.py` CRUD 读路由 + `CrudRegistry` 白名单 |
| create | `app_create_row`（`workspace/routes.py:290`）→ 校验 `create` |
| update | `app_update_row` → 校验 `update`；已有 `_owner_condition` 叠加数据范围 |
| delete | `app_delete_row` → 校验 `delete` |
| export | `app_export_rows`/`rows.xlsx` → 校验 `export` |
| import | import commit → 校验 `import` |

- 扩展 `CrudRegistry` 增加 `allowed_actions: set[str] | None`（None=全放行，用于服务身份与兼容），handler 内先校验，401/403 语义对齐既有 `PermissionDenied` → 403。
- **数据范围**：`_owner_condition` 改为读取 registry 的 `data_scope`（`own` 才施加 `created_by=当前用户`）。
- **flow / qa / dashboard**：统一调用 `resolve_access`，使数据范围一致（R8）。

### 6.3 下发与前端

- `GET /app/{id}`（`get_workspace`，`workspace/routes.py:57-133`）与 `GET /app/{id}/api/meta` 增加每页/每表 `actions` 与 `data_scope`。
- `Web/src/pages/workspace/CrudListPage.tsx`：按 `actions` 隐藏"新增/删除/编辑/导出/导入"；`CrudForm.tsx`：无 update 则只读。
- `MembersPage.tsx`：功能树每个节点展开动作开关 + 数据范围选择；调用 `PUT /deployments/{id}/members/{user_id}` 时带 `{menu_ids, actions:{menu_id:{...}}, data_scope:{menu_id:"all"|"own"}}`。
- 服务端 `members/routes.py._set_grants` 扩展为同时写 `actions_json`/`data_scope`。

### 6.4 字段级（V0.1，可选）

- `crud_configs.ui_config_json` 结构：`{"fields":{"<col>":{"hidden":["operator"],"readonly":["operator"]}}}`（角色或用户）。
- 读：`app_meta` 过滤 hidden 列；写：create/update 拒绝 readonly 列。
- 公开表单：hidden 字段不渲染；readonly 字段不提交也不报错。

---

## 七、前端改造清单

| 文件 | 改造 |
|---|---|
| `web/src/pages/deploy/BlueprintPage.tsx` | 「+ 新建表」「删除表」入口；表级提案卡；影响面展示 |
| `web/src/pages/deploy/FieldEditor.tsx` | 支持 `FILE` 类型选项；作为"新建表"的字段编辑复用 |
| `web/src/pages/workspace/CrudListPage.tsx` | 按 `actions` 裁剪按钮；FILE 列 FileChip；导入/导出入口；数据范围提示沿用 |
| `web/src/pages/workspace/CrudForm.tsx` | FILE 控件；字段级只读/隐藏 |
| `web/src/pages/workspace/MembersPage.tsx` | 功能×动作 + 数据范围 |
| `web/src/components/FileChip.tsx`（新） | 附件展示/预览/下载/删除 |
| `web/src/pages/workspace/ImportWizard.tsx`（新） | 导入四步向导 |
| `web/src/api.ts` | 新增 FilesApi / ImportApi / 结构演化 API / 权限字段类型 |

---

## 八、测试点清单

**离线 pytest（新增文件建议）**
- `test_m5_c_permissions.py`：动作 403、数据范围过滤、多函数取最宽、默认回填兼容、API key 服务身份不受限、flow/qa 一致；
- `test_m5_a_add_table.py`：悬空 FK 拒绝、重名拒绝、link 400、非创建人 403、提案==执行语句、归属列注入、页面/菜单/配置成对生成且默认不授权；
- `test_m5_a_drop_table.py`：被 FK 引用禁删、被流程引用禁删、级联无悬垂、`deployment_seed_rows` 清理、外部库快照零变化；
- `test_m5_b_files.py`：上传/下载/预览/删除、跨部署隔离、路径穿越拦截、大小/类型拒绝、越权 403、销毁清目录、pending_gc 回收、API key 不可访问文件接口；
- `test_m5_b_import.py`：列映射、各校验分支（含文件内重复与库内重复）、strict 回滚、skip_errors、模板、聚合日志=1、xlsx 读写；
- 每项完成跑**全量 pytest**（0.10.0 当前 207+ passed / 1 skipped，作为回归基线）。

**真实浏览器 Playwright**（`scripts/e2e/browser_e2e_m5x.cjs`）
- 结构演化、附件、导入导出、权限裁剪四条剧本；沿用 `serve_for_e2e.py`（waitress + LLM 桩 + 外部库种子）。

**真实库补验**：MySQL/PG 上的 DROP/CREATE、xlsx 导入、附件（文件系统与库无关，但需验证路径与权限）。

---

## 九、迁移与兼容

1. **元数据升级**：`create_all_lazy` + `_backfill_missing_columns` 自动加 `attachments` 表与两列；空值语义保证存量行为不变。
2. **存量操作员**：升级后 `resolve_access` 对空配置回填/等价于当前能力（可看、可增改删自己的数据、可导出）；不做静默收紧。
3. **存量部署**：`table_specs` 无变化；无 FILE 列的旧表不受影响。
4. **配置包导出**：新增 `attachments` 元数据是否入包？v0 **不入**（避免导出含指向文件的悬垂引用）；如需，随后续"配置包+附件 ZIP"一起。
5. **API key**：行为不变（服务身份，不校验新权限）；文档显式说明。

---

## 十、依赖与配置新增

| 项 | 变更 | 说明 |
|---|---|---|
| `requirements.txt` | `+ openpyxl>=3.1` | xlsx 导入导出；纯 Python，无重依赖 |
| `server/config.py` | `max_content_length` / `upload_allowed_ext` / `upload_allowed_mime` / `import_max_rows` | 显式上限 |
| `server/app.py` | 设 `MAX_CONTENT_LENGTH`；注册 `files`/`imports` 蓝图 | 文件接口不纳入 API key 白名单 |
| `web/package.json` | 无新增（antd `Upload`/`Image` 已有） | 不引 axios 之外的库 |

---

*版本：0.11.0 技术方案 v1 ｜ 视角：工程实现 ｜ 基线：0.10.0（M0–M4）｜ 配套：版本规划（同目录）｜ 对应裁决：DR-26~33*
