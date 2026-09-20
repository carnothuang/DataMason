# DataMason（数据瓦匠）

> **用自然语言，把一个想法一小时变成一个可运行、可交互、可查询的信息系统。**

DataMason 不是写代码的工具，而是把「想法 → 数据 → 界面 → 流程 → 工作台」整条信息化链路，压缩成一个 AI 全程伴随的对话式工作台。面向个人决策者、顾问、产品经理等不写代码但懂业务的人，快速产出可演示、可验证的真实系统。

详细产品/设计文档见 [`docs/规划/`](docs/规划/)，里程碑计划与验收见 [`docs/计划/`](docs/计划/)、[`docs/验收/`](docs/验收/)。

## 核心能力

- **秒建数据库**：快速创建并连接 SQLite / MySQL / PostgreSQL。
- **自然语言建表**：一句描述生成结构合规的表结构（含主外键规范、ER 图、蓝图 diff 提案）。
- **引用生成**：一键生成可视化 CRUD 界面、向导式业务流程、开放 API。
- **工作台 + 菜单化**：配置式组织页面与工作流，供使用者日常操作。
- **AI 伴随全程**：建库/建界面伴随 + 工作台直接问数据（只读 SQL 问答、图表）。

## 技术架构

| 层 | 技术 |
|---|---|
| 前端 | React 18 + antd 5 + Vite 5 + ECharts + react-router |
| 后端 | Flask 3 + SQLAlchemy 2 + pydantic + httpx（含 LLM 网关） |
| 元数据库 | SQLite（`datamason_meta.db`）；每套部署一个独立物理库（`data/`） |
| LLM | 通过 `server/settings` 配置的 OpenAI 兼容端点（base_url / key / model） |

## 目录结构

```
├── server/       # Flask 后端（app factory，蓝图为模块：auth/deploy/workspace/crud/flow/…）
├── web/          # React 前端（Vite，Node ≥ 18）
├── tests/        # pytest 离线测试
├── scripts/      # 冒烟 / E2E / 服务脚本（scripts/e2e/ 含 Playwright 真浏览器验收）
├── data/         # 各部署的独立物理库（SQLite）
└── datamason_meta.db  # 元数据库
```

## 启动（开发模式）

开发模式 = 后端 Flask `127.0.0.1:8765` + 前端 Vite dev server `127.0.0.1:5173`（`/api` 代理到 8765）。

前置要求：Python ≥ 3.11、Node ≥ 18。本项目**使用仓库内的专属虚拟环境 `.venv/`**（不依赖全局/托管 venv）。

### 专属虚拟环境（venv）

`.venv/` 基于 Python 3.12.9（基础解释器 `E:\python\3.12.9`）创建，依赖清单见 `requirements.txt`。

```powershell
# 首次创建（在项目根目录）
E:\python\3.12.9\python.exe -m venv .venv

# 安装后端依赖（始终用 venv 自带 pip，避免装入全局）
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 激活（可选）
.\.venv\Scripts\Activate.ps1
```

> **Playwright 例外**：E2E 真浏览器验收复用 **Node 22 上全局安装的 Playwright + Chromium**（由
> `scripts/e2e/run_browser_e2e.py` 经 `NODE_PATH` 注入），**不向 `.venv` 安装 playwright**。

### 启动

```powershell
# 1) 安装前端依赖（一次）
npm --prefix web install

# 2) 启动后端（终端 A，显式引用专属 venv）
.\.venv\Scripts\python.exe -m flask --app server.app:create_app run --port 8765

# 3) 启动前端（终端 B，Node ≥ 18）
npm --prefix web run dev
```

访问 http://127.0.0.1:5173

### 启动后验证

```bash
Invoke-RestMethod http://127.0.0.1:8765/api/v1/health   # 应返回 {"status":"ok",...}
```

### 常见坑

- **后端 8765 起不来**：多为依赖缺失。用 `.\.venv\Scripts\python.exe -m pip install -r requirements.txt` 补装（勿用全局 `pip`）；仍缺时按报错单独补装，例如 `openpyxl`、`sqlglot`、`waitress`。
- **用错解释器**：务必显式调用 `.\.venv\Scripts\python.exe`（或先 `Activate.ps1`），避免误用全局 Python 导致依赖缺失。
- **首次启动**：无用户时业务 API 返回 409，需先通过登录/引导创建管理员账号；有 `datamason_meta.db` 则直接可用。
- **LLM**：无模型配置时部署向导不可用，需在设置页配置 OpenAI 兼容端点。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest      # 离线全量（tests/）
npm --prefix web run build               # 前端类型检查 + 构建
```

生产模式：`npm --prefix web run build` 后仅启动后端（专属 venv）即可单端口托管（Flask 自动托管 `web/dist`）。