# CLAUDE.md

本项目的完整前端交互与布局规范以以下文件为准，编辑 `dashboard/src/**` 前**先读**：

- 真相源：`DESIGN.md`（从 WorkMason 继承的前端业务交互设计）
- 入口规则：`AGENTS.md` [§12 前端交互继承](AGENTS.md)（MUST 清单、DataMason 适配表、照抄范式、自检）

严格遵守 `AGENTS.md` §12 的「硬性约束」与 [§8「不要做」](AGENTS.md)。

## 硬约束（违反即返工）

- 颜色 / 圆角 / 间距 / 阴影用 `--fn-*` CSS 变量（`dashboard/src/styles/theme.css`），禁止硬编码 `#hex` / `rgb()` / 魔法 px；ECharts/canvas 与 antd token 经 `dashboard/src/styles/tokens.ts` 运行时读取。
- 组件禁止直接 `fetch` / `axios` / `WebSocket`，一律走 `dashboard/src/api.ts`。
- 用户可见文案走 i18n（`dashboard/src/locales/{en,zh}.json`，zh/en 键对齐）；禁止硬编码用户可见中文。
- 实体新增 / 编辑用 antd `Drawer`；列表提供卡片 / 表格切换并 `localStorage` 持久化。
- 只编辑 `dashboard/src/`；**不要手改 `dashboard/dist/`**；改 UI 后 `npm --prefix dashboard run build`。

不确定时查真相源，不要自行发挥；查不到就停下来问。

## 常用命令

```powershell
# 开发：后端 8765 + 前端 Vite 5173
.\.venv\Scripts\python.exe -m flask --app datamason.app:create_app run --port 8765
npm --prefix dashboard run dev

# 前端唯一验证手段（tsc --noEmit && vite build）
npm --prefix dashboard run build

# 后端离线全量测试
.\.venv\Scripts\python.exe -m pytest
```
