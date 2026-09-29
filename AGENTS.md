# AGENTS.md

本仓库 AI 编码代理的导航指南。面向 **DataMason（数据瓦匠）** —— Flask + React 的对话式信息系统生成平台。

> 本文替代旧的 WorkMason 模板（FastAPI / `src/workmason/` / uv / make / i18n）。若发现文档与代码冲突，以代码与 `docs/` 为准，并遵循 [§10](#10-变更流程) 的 DR 纪律回写。

## 1. 协作原则

> 宁慢勿错；琐碎任务可放宽。与 [§10 变更流程](#10-变更流程)、[§11 沟通](#11-沟通) 配套。

### 先想再写

- 先声明假设；不确定就问，不要猜。
- 存在多种解读时，列出让用户选，不要默默替他决定。
- 有更简单的做法就提出来；该反对就反对。
- 卡住了就停下，明确指出卡在哪。

### 简单优先

- 只写解决问题所需的最少代码；不加未被要求的特性、抽象或配置项。
- 不为现实中不会发生的情况加防御性错误处理。
- diff 无谓变大时主动收敛。

### 外科手术式修改

- 只碰与任务直接相关的行；不顺手"清理"周边代码、注释或格式。
- 不因为风格不同就重构能跑的代码。
- 无关的死代码：指出即可，不要主动删除。
- 只清理由**你**引入的孤立 import / 变量 / 函数。

### 可验证的结果

- 把任务转成可验证目标（测什么、哪条命令证明成功）。
- 多步工作先给短计划：`步骤 → 验证：…`。
- 说"完成"前给验证证据；默认交付线是 **离线全量 `pytest` 全绿 + `npm --prefix dashboard run build` 通过**（见 [§6](#6-运行命令)、[§10](#10-变更流程)）。

## 2. 这是什么

**DataMason** —— 用自然语言把"想法 → 数据 → 界面 → 流程 → 工作台"压缩成对话式工作台，面向不写代码但懂业务的人，快速产出可演示、可验证的真实系统。

- **秒建数据库**：创建并连接 SQLite / MySQL / PostgreSQL。
- **自然语言建表**：描述生成结构合规的表（主外键规范、ER 图、蓝图 diff 提案）。
- **引用生成**：生成可视化 CRUD 界面、向导式业务流程、开放 API。
- **工作台 + 菜单化**：配置式组织页面与工作流。
- **AI 伴随全程**：建库/建界面伴随 + 工作台直接问数据（只读 SQL 问答、图表）。

产品/设计文档见 [`docs/规划/`](docs/规划/)，里程碑计划与验收见 [`docs/计划/`](docs/计划/)、[`docs/验收/`](docs/验收/)。

## 3. 技术栈

| 层 | 技术 |
|----|------|
| 语言 | Python 3.12（仓库内专属 `.venv/`） |
| Web 框架 | Flask 3（应用工厂 + 蓝图） |
| ORM | SQLAlchemy 2（`Mapped` / `mapped_column`） |
| 校验 | pydantic 2（规格强校验） |
| HTTP 客户端 | httpx（LLM 网关） |
| 加密 | cryptography（Fernet，`src/datamason/security.py`） |
| 元数据库 | SQLite（`datamason_meta.db`） |
| 业务库 | 每套部署一个独立物理库（SQLite / MySQL / PostgreSQL），`data/` |
| 前端 | React 18 + antd 5 + Vite 5 + ECharts + react-router 6 |
| 生产 WSGI | waitress（`scripts/serve_walkthrough.py`） |
| LLM | 设置页配置的 OpenAI 兼容端点（base_url / key / model） |
| 测试 | pytest（扁平 `tests/`，里程碑命名） |

**本仓库不使用 uv、Makefile、alembic、前端 i18n 框架。** 不要照搬其他项目的 `uv run` / `make all` 约定。

## 4. 目录结构

```
src/datamason/                  Flask 后端
  app.py                 应用工厂 create_app：注册蓝图 / 鉴权守卫 / SPA 托管
  config.py              Config dataclass + 环境变量
  security.py            Cipher（Fernet 加解密）
  models/__init__.py     元数据库 ORM 模型 + create_all_lazy（惰性建表 + 列回填）
  auth/                  登录、会话、账号
  settings/              LLM 模型 / 数据连接 / Redis 配置中心（平台管理员）
  deploy/                部署管线（preview/confirm/link/结构演化/导出）
  workspace/             运行态工作台（菜单树 + 业务 CRUD 路由）
  members/               成员与权限（三层角色 + 按人授权）
  flow/                  向导式流程（flow_specs + 每用户游标）
  public/                访客公开表单（匿名）
  share/                 分享链接
  files/                 附件（上传/预览/回收）
  imports/               CSV/XLSX 导入 + XLSX 导出
  apiaccess/             部署级 API key 发放/认证
  qa/                    问数据（NL→只读 SQL）+ 钉住报表
  ai/                    AI 生成历史治理（统一埋点）
  crud/                  元数据驱动反射 CRUD 引擎（纯领域，无路由）
  dataaccess/            多方言数据层（纯领域）
  ddl/                   规格→DDL / 迁移 / 骨架（纯领域，无 LLM）
  llm/                   LLM 网关 + 结构/骨架生成（纯领域）
  audit/                 两阶段操作日志（无路由）
dashboard/                     前端源码（Vite，Node ≥ 18）
  public/                静态资源根（Vite publicDir，原样拷入 dist 根）
    favicon.svg          站点图标（SVG 占位）
    logo.svg             品牌 logo（PWA / 引用，SVG 占位）
    emblem.png           六边形徽章（侧栏折叠态 / 登录页）
    logo-light.png       横版品牌 Logo（浅色主题）
    logo-dark.png        横版品牌 Logo（深色主题）
    mascot.png           品牌吉祥物（空状态）
    manifest.json        Web App Manifest
  src/api.ts             单一 axios 客户端 + 全部类型 + 全部 API 分组
  src/App.tsx            鉴权门 + 路径分发 + 主壳（侧边栏/路由）
  src/assets/            经构建处理的静态资源（占位 logo，可替换/扩充）
  src/styles/            全局样式与设计 token（theme.css：light/dark --fn-*）
  src/context/           React Context（ThemeContext：明/暗/跟随系统）
  src/layouts/           布局骨架（PageShell：标题行 + 内容容器）
  src/components/        共享展示组件（ChartCard/ErCanvas/ErGraph/FileChip/MaximizableModal）
  src/pages/             路由级页面（Login/Setup/Deployments/Settings/Public/Join…）
  src/pages/deploy/      建库特性组（DeployWizard/HybridBuilder/Blueprint/FieldEditor…）
  src/pages/settings/    设置页各 Tab
  src/pages/workspace/   运行态工作台（Workspace/CRUD/Flow/QA/Members/Logs/ApiDocs…）
tests/                   pytest（扁平，test_m<里程碑>_<特性>.py）
scripts/                 冒烟 / HTTP 点验 / E2E / 演示服务
  e2e/                   Playwright 真浏览器 E2E（serve_for_e2e.py + run_browser_e2e.py + *.cjs）
docs/规划/               版本规划 / 技术方案 / 决策记录（DR）
docs/计划/               按日期的开发计划
docs/验收/               按日期的验收报告
data/                    各部署独立物理库（SQLite）
datamason_meta.db        元数据库
start.ps1                快速启动：前后端依赖安装 / 编译 / 部署
```

## 5. 模块边界

**总原则：依赖向内。** 传输层调用领域，领域不反向调用 HTTP/CLI。

```
dashboard/ ──HTTP(/api/v1)──► src/datamason/*/routes.py ──► src/datamason/*/service.py
                                                └─► src/datamason/{crud,dataaccess,ddl,llm,audit}/
                                                       └─► src/datamason/models/, security, config
```

`src/datamason/app.py` 是唯一装配点：导入各子包 `bp`、注册 URL 前缀、安装全局鉴权守卫与 SPA 托管。

### `src/datamason/` 子包职责

| 路径 | 拥有 | 不拥有 |
|------|------|--------|
| `app.py` | 应用工厂、蓝图注册、鉴权守卫、SPA 托管、JSON 序列化 | 业务规则、SQL |
| `config.py` | `Config` + 环境变量解析 | 任何 `server.*` 领域代码 |
| `security.py` | `Cipher`（连接串 / LLM key / API key 的 Fernet 加解密） | 口令哈希（在 `auth/service.py`） |
| `models/` | 元数据库 ORM 模型 + `create_all_lazy` | 业务库、HTTP |
| `auth/ settings/ deploy/ workspace/ members/ flow/ public/ share/ files/ imports/ apiaccess/ qa/ ai/` | 各资源蓝图（`routes.py`）+ 领域服务（`service.py`） | 其他子包的内部实现 |
| `crud/` | 反射 CRUD 引擎（`engine.py`）+ 引擎/注册表装配（`runtime.py`） | Flask 蓝图（由 `workspace` 暴露） |
| `dataaccess/` | 多方言引擎、建库/执行 DDL、大 schema 选表 | 业务语义 |
| `ddl/` | `table_spec` 强校验、`ddl_builder` 建表、`migration` 改列、`skeleton` 混合骨架 | LLM 调用、落库 |
| `llm/` | OpenAI 兼容网关 + 结构/骨架/字段生成（`MAX_RETRY=2`） | 直接落库；只产 JSON 中间规格 |
| `audit/` | 两阶段操作日志（`pending`→`committed`）+ 启动对账 | HTTP 路由 |

### `dashboard/src/` 职责

| 路径 | 拥有 | 不拥有 |
|------|------|--------|
| `api.ts` | 单一 axios 实例、全部请求/响应类型、全部 API 分组 | UI、React 状态 |
| `App.tsx` | 鉴权门、路径分发、主壳、`/deployments` + `/settings` 路由 | 特性业务逻辑 |
| `assets/` | 经构建处理的图片等静态资源 | 运行时按绝对 URL 引用的资源（放 `public/`） |
| `styles/` | 全局样式、`--fn-*` 设计 token、antd 覆盖 | 组件级内联样式 |
| `context/` | 跨层 React Context（主题偏好） | 页面局部状态 |
| `layouts/` | 页面级布局骨架（PageShell） | 业务数据请求 |
| `components/` | 可复用展示组件 | 页面状态、直接 `fetch`（只经 `api.ts`） |
| `pages/`（根） | 独立路由页（登录/首启/公开/加入/控制台/设置）+ 弹窗 | 通用小部件 |
| `pages/deploy/` | 建库特性组 | 运行态工作台 |
| `pages/settings/` | 设置页每 Tab | 跨 Tab 编排 |
| `pages/workspace/` | 运行态工作台与工程室工具 | 部署引导 |
| `public/` | 不经过构建处理、按绝对 URL 提供的静态资源 | 需要被 import 参与打包的资源（放 `src/assets/`） |
| `dist/` | Flask 托管的前端构建产物 | **不要手改**，源码在 `dashboard/src/` |

### 硬性禁令

- `src/datamason/*/routes.py` 只做 HTTP：解析参数、鉴权/角色校验、调服务、映射错误；**不写业务规则**。
- 领域逻辑不进路由；服务函数以 `app` 为首参（见 [§7](#7-关键模式)）。
- `src/datamason/{crud,dataaccess,ddl,llm,audit}/` 是纯领域/数据层，**不 import 蓝图、不 import `request`**。
- 业务库引擎按请求创建，**必须 `finally` dispose**，不得长期持有或泄漏。
- `dashboard/src` 组件不得直接 `fetch`/`axios`，统一走 `api.ts`。
- **不要编辑 `dashboard/dist/`**（构建产物）；改前端只改 `dashboard/src/`，再 `npm --prefix dashboard run build`。
- LLM **只产 JSON 中间规格**；DDL / 建库 / 种子 / 页面一律由 Python 确定性映射。
- 直连（link）模式外部库**只读**，禁止任何写库/结构变更。
- 不新增前端运行时依赖（无 DR 不引，见 DR-36）。
- 不引入 alembic / 新框架 / 新测试框架。

## 6. 运行命令

后端 Python **一律用仓库内专属 venv**（`.\.venv\Scripts\python.exe`），不要用全局/托管 Python。

```powershell
# 依赖安装（一次）
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm --prefix dashboard install

# 开发模式：后端 8765 + 前端 Vite 5173（/api 代理到 8765）
.\.venv\Scripts\python.exe -m flask --app datamason.app:create_app run --port 8765
npm --prefix dashboard run dev

# 一键（推荐）：dev / prod / build
.\start.ps1                 # 开发：装依赖 + 起前后端 + 健康检查 + 开浏览器
.\start.ps1 -Mode prod      # 生产：构建前端 + waitress 单端口托管 dashboard/dist
.\start.ps1 -Mode build     # 仅装依赖 + 构建前端

# 生产/演示单端口（真实元数据库 + 真实 LLM 配置）
.\.venv\Scripts\python.exe scripts\serve_walkthrough.py [port]   # 默认 8765

# 测试（离线全量，不调 LLM）
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m pytest tests\test_m6_5_merge.py -q

# 前端类型检查 + 构建（前端唯一验证手段）
npm --prefix dashboard run build

# 健康检查
Invoke-RestMethod http://127.0.0.1:8765/api/v1/health   # {"status":"ok",...}

# 真实 LLM / 真库冒烟（需环境变量，见脚本头部）
.\.venv\Scripts\python.exe scripts\smoke_llm.py
.\.venv\Scripts\python.exe scripts\smoke_mysql.py      # DATAMASON_TEST_MYSQL_URL
.\.venv\Scripts\python.exe scripts\smoke_pg.py         # DATAMASON_TEST_PG_URL
.\.venv\Scripts\python.exe scripts\smoke_qa.py
.\.venv\Scripts\python.exe scripts\run_e2e.py          # 自起服务 + 真实端到端

# 真浏览器 E2E（Playwright 走全局 Node，不装进 .venv）
$env:DATAMASON_E2E_LLM_STUB="1"
.\.venv\Scripts\python.exe scripts\e2e\run_browser_e2e.py browser_e2e_m6.cjs

# 单点 HTTP 点验（各自起隔离服务）
.\.venv\Scripts\python.exe scripts\e2e\http_check_m41.py
```

**端口约定**：后端 `8765`，前端 `5173`（Vite `/api` 代理固定指向 `8765`，改端口需同步改 `dashboard/vite.config.ts`）。

**Playwright 例外**：E2E 复用 Node 全局安装的 Playwright + Chromium（`run_browser_e2e.py` 经 `NODE_PATH` 注入），**不向 `.venv` 安装 playwright**。

## 7. 关键模式

**应用工厂与扩展。** `create_app(config_object=None)`（`src/datamason/app.py:29`）。测试通过 `Config(meta_db_uri=…, data_root=…)` 注入隔离环境（`tests/conftest.py:10`）。`app.extensions` 仅三个键：

| 键 | 位置 | 值 |
|----|------|----|
| `cipher` | `src/datamason/app.py:40` | `Cipher(data_root)` |
| `datamason_engine` | `src/datamason/models/__init__.py:353` | 元数据库 `Engine` |
| `datamason_session_factory` | `src/datamason/models/__init__.py:354` | 元数据库 `sessionmaker` |

`app.config["_dm_initialized"]` 是"系统是否已初始化"的缓存标志，不是扩展。

**元数据库 vs 业务库。** 元数据库单引擎；业务库每套部署一个物理库，按请求从**解密后**的连接串新建引擎，用完 `finally` dispose（`src/datamason/crud/runtime.py:17`）。业务库**不**注册为持久扩展。

```python
Session = app.extensions["datamason_session_factory"]
with Session() as s:
    ...
```

**无 alembic 的 schema 演化。** 元数据库靠 `create_all(checkfirst=True)` + `_backfill_missing_columns`（`src/datamason/models/__init__.py:357`，幂等 `ALTER TABLE ADD COLUMN`，仅补可空/有默认列）。业务库建表走 `ddl/ddl_builder.build_schema_ddl`，改列走 `ddl/migration.build_migration`。新增表/列时，模型与回填逻辑要一起改，且保持幂等。

**蓝图 → 服务 → 领域。** 一个子包一个模块级 `bp = Blueprint(...)`（少数模块有两个，如 `qa/engineer_bp`、`ai/settings_bp`/`deploy_bp`）。路由薄：解析 → 取 `session.get("user_id")` → 角色校验 → 调服务（传 `current_app` 为首参）→ `jsonify`。服务是**自由函数**（非类），从 `app.extensions` 取会话。

**错误处理。** 每个模块自定义异常（`DeployError` / `FlowError` / `QaError` / `PublicError` / `ShareError` / `ApiKeyError` / `FileError` / `ImportError_` / `MigrationError` / `DataAccessError` / `SetupError` / `CryptoError` 等）+ 共享 `PermissionDenied`，在蓝图内用 `@bp.errorhandler` 映射为 HTTP + `{"error": …}`。

**鉴权与授权。** 全局 `before_request` 守卫（`src/datamason/app.py:106`）：`/api/*` 需登录（白名单：健康检查/登录/首启向导；`/api/v1/public/*` 匿名放行）；未初始化系统返回 409；`/api/v1/settings/`、`/api/v1/auth/users` 需平台管理员。会话是 Flask signed cookie（`session["user_id"]`）。三层角色 `creator/admin/operator` 与按人授权在 `src/datamason/members/service.py`，业务路由复用其 `assert_member` / `require_role` / `assert_action`。

**加密与密钥。** `Cipher`（`src/datamason/security.py:33`）加密连接串、LLM key、API key；密钥来源 `DATAMASON_FERNET_KEY`，否则 `{DATA_ROOT}/.secret_key` 自动生成。口令哈希是 PBKDF2-HMAC-SHA256（`src/datamason/auth/service.py:15`）。API key 形如 `dm_<随机>`，`X-API-Key` 或 `Bearer` 提交，仅可访问该部署的 `/api/v1/app/{id}/api/tables/*` 与 `/api/meta`（`src/datamason/app.py:149`、`src/datamason/apiaccess/service.py:104`）。

**归属列（系统保留）。** `created_by/created_at/updated_by/updated_at` 由 `ddl/ddl_builder.inject_ownership_columns` 注入，客户端不可写（`src/datamason/crud/engine.py:327`），用于"我的记录"数据范围。

**审计两阶段。** 业务写前在元数据库记 `pending`，写成功后置 `committed`；崩溃遗留由 `reconcile_pending` + 启动后台线程收尾（`src/datamason/audit/service.py`、`src/datamason/app.py:80`）。`actor_label` 用于 API key 身份显示。

**LLM 只产中间规格。** `OpenAiCompatibleClient`（`src/datamason/llm/client.py:41`，测试可注入 `transport`）→ `chat_json` 返回 `(json, Usage)`；`_loads_llm_json` 容忍 markdown 围栏/尾随逗号。默认客户端由 `settings/service.get_default_llm_client`（`src/datamason/settings/service.py:331`）解析（模型行优先，回退环境变量 `DATAMASON_LLM_*`，超时默认 300s）。生成入口在 `src/datamason/llm/structure.py`（`generate_schema` / `generate_skeleton` / `generate_table_columns` / `enrich_labels` / `generate_seed_rows`），失败回喂重试 `MAX_RETRY=2`。每次生成经 `src/datamason/ai/service.record` 写 `ai_generations` 埋点（失败静默）。

**问数据只读门。** `qa/service.enforce_readonly`（`src/datamason/qa/service.py:168`）用 sqlglot 强制单条 SELECT、表白名单、禁 DML/DDL/事务/`SELECT INTO`/锁/跨库，并加外层 `LIMIT`。

**前端 API 单文件。** 所有请求集中在 `dashboard/src/api.ts`：`http = axios.create({ baseURL: "/api/v1" })`，响应拦截器统一处理 401（清 `datamason_me` 并跳 `/login`，`/public/*` 除外）与错误信封。类型与 API 分组同文件声明（`DeployApi` / `CrudApi` / `QaApi` / `MembersApi` / …）。鉴权靠同源 cookie，无 `Authorization` 头。新增接口就在这里加分组 + 类型。

**测试约定。**

- 扁平目录，命名 **`test_m<里程碑>_<特性>.py`**（如 `test_m6_5_merge.py`），文件 docstring 标注任务号与 DR 号。
- 唯一夹具 `tests/conftest.py`：`app` / `client`，用 `tmp_path` 隔离元数据库与 `data_root`。
- LLM 桩两种主流方式：① monkeypatch `server.settings.service.get_default_llm_client` 返回 `FakeLlm`（含 `chat_json`）；② 直接 `OpenAiCompatibleClient(cfg, transport=httpx.MockTransport(...))`。
- 真 LLM / 真库用例用 `@pytest.mark.skipif` 或 `pytest.skip` 环境变量门控（`DATAMASON_RUN_LLM` / `DATAMASON_TEST_MYSQL_URL` / `DATAMASON_TEST_PG_URL`）。
- 业务库断言经 `Path(app.config["DATA_ROOT"])`，不要写死绝对路径。

**文档与里程碑。**

- 版本 ↔ 里程碑：**0.10.0 = M0–M4**，**0.11.0 = M5**（0.11.1 = 验收修复/文档基线），**0.12.0 = M6**；M7 预留。
- 标识符：`M<大>-<任务>[-<子>]`（如 `M6-3-1`）；版本级验收为 `M*-9` / `M6-6`；产品侧编号 `P0..P5`。
- `DR-XX` = 决策记录，权威在《产品一页纸》§九（0.10.0 冻结 DR-01~25，后续版本各自追加）。**DR 优先**：跨文档的概念变更（状态/角色/数据模型/能力范围）先追加/更新 DR，再改规划/计划/代码。
- 文档分三类：`docs/规划/<版本>/`（版本规划、技术方案与实现闭环、决策记录）、`docs/计划/<YYYYMMDD>/开发计划.md`、`docs/验收/YYYYMMDD-<版本>-项目验收报告.md`（+ 独立验收报告）。

## 8. 不要做

边界规则见 [§5](#5-模块边界)。此外：

- **不要用 `uv` / `make` / `uv run pytest`** —— 本仓库用 `.\.venv\Scripts\python.exe -m pytest`。
- 不要用全局或托管 Python；不要 `pip install` 到全局。
- 不要在 `src/datamason/*/routes.py` 写业务规则，或从路由直接 import 领域内部实现（走 `service.py`）。
- 不要把服务/领域里的**惰性 import 提到模块顶层** —— 服务↔路由↔模型存在真实循环依赖，顶层导入会炸。
- 不要在 async 之外做阻塞 I/O 于热路径；LLM/DB 调用按既有同步风格并设超时。
- 不要长期持有业务库引擎或忘记 `dispose()`。
- 不要手改 `dashboard/dist/`；不要在 `dashboard/src` 直接发 HTTP（统一 `api.ts`）。
- 不要引入前端 i18n 框架或后端 gettext；**当前所有用户可见文案是硬编码中文**，新增文案沿用中文。
- 不要引入 alembic 或新框架/新测试框架；schema 变更用既有惰性建表 + 列回填。
- 不要在 link（直连）模式写库或改结构。
- 不要新增前端运行时依赖（无 DR 不引；i18n 的 `i18next` / `react-i18next` 为已批准例外）。
- 不要让 LLM 产出 DDL / 直接落库；只允许 JSON 中间规格 + Python 确定性映射。
- 不要在离线测试里调用真实 LLM 或真库；用桩 + 环境变量门控。

## 9. 去哪找

| 问题 | 位置 |
|------|------|
| 应用如何装配 / 蓝图前缀 | `src/datamason/app.py` |
| 配置与环境变量 | `src/datamason/config.py` |
| 元数据库有哪些表 | `src/datamason/models/__init__.py` |
| 建表 / 改列 / 迁移 | `src/datamason/ddl/`（`ddl_builder` / `migration` / `table_spec` / `skeleton`） |
| 反射 CRUD 引擎 | `src/datamason/crud/engine.py`、`src/datamason/crud/runtime.py` |
| 部署管线（preview/confirm/link/结构演化） | `src/datamason/deploy/service.py`、`src/datamason/deploy/routes.py` |
| 运行态工作台与业务 CRUD 路由 | `src/datamason/workspace/routes.py` |
| 权限 / 角色 / 按人授权 | `src/datamason/members/service.py` |
| 鉴权与会话 | `src/datamason/app.py`（守卫）、`src/datamason/auth/` |
| 加密 / 密钥 | `src/datamason/security.py`、`src/datamason/auth/service.py` |
| LLM 网关与生成 | `src/datamason/llm/client.py`、`src/datamason/llm/structure.py`、`src/datamason/settings/service.py` |
| 问数据只读门 | `src/datamason/qa/service.py` |
| 附件 / 导入导出 | `src/datamason/files/service.py`、`src/datamason/imports/service.py` |
| API key 通道 | `src/datamason/apiaccess/service.py` |
| 操作日志 / 对账 | `src/datamason/audit/service.py` |
| 前端如何调 API | `dashboard/src/api.ts` |
| 前端路由与鉴权门 | `dashboard/src/App.tsx` |
| 前端构建配置 | `dashboard/vite.config.ts`、`dashboard/package.json` |
| 前端交互规范 / 真相源 | `DESIGN.md`、`AGENTS.md` [§12](#12-前端交互继承workmason-规范) |
| 前端入口规则（多工具） | `CLAUDE.md`、`.cursor/rules/00-inherit.mdc`、`.github/copilot-instructions.md` |
| 设计 token / 运行时读取 | `dashboard/src/styles/theme.css`、`dashboard/src/styles/tokens.ts` |
| 测试夹具与 LLM 桩 | `tests/conftest.py` + 各 `tests/test_m*.py` |
| E2E 真浏览器 | `scripts/e2e/serve_for_e2e.py`、`scripts/e2e/run_browser_e2e.py`、`scripts/e2e/browser_e2e*.cjs` |
| 演示 / 单端口服务 | `scripts/serve_walkthrough.py` |
| 一键启动 | `start.ps1` |
| 版本规划 / 决策记录 | `docs/规划/` |
| 开发计划 | `docs/计划/<YYYYMMDD>/开发计划.md` |
| 验收报告 | `docs/验收/` |

## 10. 变更流程

1. **澄清范围** —— 先读相关代码/文档；假设与歧义先与用户确认（见 [§1](#1-协作原则)）。
2. **DR 优先** —— 涉及跨文档概念（状态/角色/数据模型/能力范围）的改动，先在《产品一页纸》§九追加/更新 DR，再动代码与规划文档。
3. **最小实现** —— 只改任务相关文件；前端源码在 `dashboard/`，构建产物在 `dashboard/dist/`（改 UI 后 `npm --prefix dashboard run build`）。
4. **验证** —— 后端/交付线：`.\.venv\Scripts\python.exe -m pytest` 全绿；前端：`npm --prefix dashboard run build`（`tsc --noEmit && vite build`）。里程碑任务再加真实点验（HTTP 点验 / 真浏览器 E2E）。跨平台注意：Windows 下不要写死 POSIX 路径，用 `tmp_path` / `Path`。
5. **收尾** —— 清理本次引入的孤立符号；**未经明确要求不要 commit / push**。

### 提交与发布

- 提交信息用 `<type>(<任务号>): <中文摘要>`，如 `feat(M6-5): …`、`fix(M6-4): …`、`docs(...)`、`test(...)`、`chore(...)`、`plan(...)`。**每个任务一次 commit**（信息含任务号），保持可回滚。
- 仓库长期分支为 **`main`**（Gitea 远端）；版本以 **tag** 发布（现有 `0.10.0`…`0.12.1`）。发布后 tag 打在 `main` 上。
- 每完成一项任务：先跑全量 `pytest` + 前端构建，全绿再进下一项。

## 11. 沟通

- 默认用**中文**与用户交流；引用代码用 `` `path:line` ``。
- 先结论后细节；写完整句子，不写电报体。
- 标记完成时给出验证命令与结果（或说明为何未跑）。
- 不堆无关的后续建议；范围外问题点到为止，不擅自扩大改动。

## 12. 前端交互继承（WorkMason 规范）

> 权威真相源是仓库根目录的 [`DESIGN.md`](DESIGN.md)（从 WorkMason 继承来的前端业务交互设计）。
> 编辑 `dashboard/src/**` 前先读它。本仓库**只保留这一套交互范式**，不要另起炉灶。

### 12.1 继承范围（DataMason 适配）

DESIGN.md 描述的是 WorkMason Dashboard 的实现（`dashboard/src`）。DataMason 前端沿用其**交互与布局范式**，
但下列差异按本仓库既有政策处理，**不照搬**：

| DESIGN.md 要求 | DataMason 适配 |
|---|---|
| `dashboard/src`、`src/api/` 多模块 | `dashboard/src/`；所有请求集中在单文件 `dashboard/src/api.ts`（`http = axios.create({ baseURL: "/api/v1" })`） |
| i18n 全覆盖（`t()`、en/zh 对齐） | **已对齐 Mason 标准**：引入 i18next + react-i18next；文案集中在 `dashboard/src/locales/{en,zh}.json`，zh/en 键对齐；禁止硬编码用户可见文案 |
| Monaco / xterm / virtuoso / recharts / PWA 等依赖 | **不新增前端运行时依赖**（无 DR 不引）；ECharts 是既有依赖。i18n 的 `i18next` / `react-i18next` 为已批准例外 |
| `permissions.ts` / `RequirePermission` | 复用既有权限模型：后端在 `src/datamason/members`，前端形如 `user.is_platform_admin` / 工作台 `role`（`creator`/`admin`/`operator`） |
| 服务端时区 `formatServer*` | DataMason 无此机制；按后端返回字符串原样展示，必要时用 `dayjs` |
| `MainLayout` / `sidebarNav` | 控制台外壳在 `dashboard/src/App.tsx`（`ConsoleShell`）；工作台外壳在 `dashboard/src/pages/workspace/Workspace.tsx` |

### 12.2 硬性约束（MUST，违反即返工）

- 颜色 / 圆角 / 间距 / 阴影一律用 `--fn-*` CSS 变量（`dashboard/src/styles/theme.css`），
  禁止在组件里硬编码 `#hex` / `rgb()` / 魔法 px。ECharts/canvas 与 antd 主题 token 只接受字面色值，
  一律经 `dashboard/src/styles/tokens.ts` 从 `--fn-*` 运行时读取。
- 组件禁止直接 `fetch` / `axios` / 开 `WebSocket`，一律走 `dashboard/src/api.ts`。
- 用户可见文案一律走 i18n（`useTranslation` + `dashboard/src/locales/{en,zh}.json`），zh/en 键必须对齐；禁止硬编码用户可见中文（代码注释除外）。
- 实体新增 / 编辑用 antd `Drawer`（与既有页面一致）；不要为实体 CRUD 新增路由。
- 列表页默认提供卡片 / 表格视图切换，选择用 `localStorage` 持久化（键前缀 `datamason:`）。
- 重度运行面板（工作台、流程编排、表单设计器）用 keep-alive（`display:none` 切换），避免离开返回重挂载。
- 每个列表 / 区块都要有明确的加载态（`Spin`）与空态（`Empty`）。
- 只编辑 `dashboard/src/`；改 UI 后 `npm --prefix dashboard run build`。**不要手改 `dashboard/dist/`**。

### 12.3 照抄范式（不要重新发明）

- 应用外壳：`dashboard/src/layouts/MainLayout.tsx`（控制台、移植自 WorkMason `MainLayout`）
- 侧栏 / 导航：`dashboard/src/layouts/Sidebar.tsx` + `SidebarCollapsedIconNav.tsx` + `Sidebar.css`（分组可折叠、折叠态图标导航、移动端抽屉）
- 拖拽边：`dashboard/src/components/RailEdgeControl.tsx`（移植自 WorkMason）
- 移动端顶栏：`dashboard/src/layouts/Header.tsx`
- 页面骨架：`dashboard/src/layouts/PageShell.tsx`（标题行 + 内容卡）
- 运行态工作台外壳：`dashboard/src/pages/workspace/Workspace.tsx`（复用同一套 `Sidebar` + `RailEdgeControl` + `Header`）
- 主题 / 品牌色：`dashboard/src/context/ThemeContext.tsx` + `dashboard/src/styles/theme.css`
- HTTP / 鉴权封装：`dashboard/src/api.ts`
- 国际化：`dashboard/src/i18n.ts` + `dashboard/src/locales/{en,zh}.json`
- 关系图 / 字段表：`dashboard/src/components/ErGraph.tsx`、`dashboard/src/components/ErCanvas.tsx`

### 12.4 完成前自检

- [ ] 无新增硬编码颜色 / 魔法 px
- [ ] 请求只经 `dashboard/src/api.ts`
- [ ] 实体 CRUD 用 `Drawer`，列表有卡片 / 表格切换且持久化
- [ ] 新增文案已入 `dashboard/src/locales/{en,zh}.json` 且 zh/en 键对齐
- [ ] 有加载态与空态
- [ ] `npm --prefix dashboard run build` 通过
