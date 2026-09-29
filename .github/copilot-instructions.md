# DataMason — Copilot 前端规范

编辑 `dashboard/src/**` 时，必须遵循 WorkMason 的交互与布局规范：

- 真相源：`DESIGN.md`（从 WorkMason 继承的前端业务交互设计）
- 适配与完整 MUST 清单：`AGENTS.md` §12「前端交互继承」

## 硬约束

- 颜色 / 圆角 / 间距 / 阴影一律用 `--fn-*` CSS 变量（`dashboard/src/styles/theme.css`），禁止硬编码 `#hex` / `rgb()` / 魔法 px；ECharts/canvas 与 antd token 经 `dashboard/src/styles/tokens.ts` 运行时读取。
- 组件禁止直接 `fetch` / `axios` / `WebSocket`，一律走 `dashboard/src/api.ts`。
- 用户可见文案走 i18n（`dashboard/src/locales/{en,zh}.json`），zh/en 键对齐；禁止硬编码用户可见中文。
- 实体新增 / 编辑用 antd `Drawer`，不要为实体 CRUD 新增路由。
- 列表默认提供卡片 / 表格切换并 `localStorage` 持久化。
- 只编辑 `dashboard/src/`；不要手改 `dashboard/dist/`；改 UI 后 `npm --prefix dashboard run build`。

## 交互范式

- 新建页面：套用 `dashboard/src/layouts/PageShell.tsx` 骨架。
- 实体 CRUD：优先 antd `Drawer`。
- 列表：卡片 / 表格切换并持久化；每个列表有加载态（`Spin`）与空态（`Empty`）。
- 重度面板：keep-alive（`display:none`），避免重挂载。

不确定时查真相源，不要自行发挥。
