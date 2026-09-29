# <PROJECT> — Copilot 前端规范

编辑前端代码时，必须遵循 WorkMason 的交互与布局规范：

- 真相源：`DESIGN.md`
- 完整 MUST 清单与禁止项：`AGENTS.md`

## 硬约束

- 颜色 / 圆角 / 间距 / 阴影一律用 `--fn-*` CSS 变量，禁止硬编码 `#hex` / `rgb()` / 魔法 px。
- 组件禁止直接 `fetch` / `WebSocket`，一律走 `src/api/`。
- 用户可见文案走 i18n（`t()`），`en` / `zh` 键必须对齐。
- 时间显示用服务端时区（`formatServer*`），禁止裸 `toLocaleString()`。
- 权限走 `permissions.ts` 的 `userCan` / `RequirePermission`。
- API 错误走 `parseApiError` / `apiErrorMessage`。

## 交互范式

- 新建页面用 `PageShell` + `MainLayout`，并注册到 `routes/index.tsx`。
- 实体 CRUD 优先用 antd `Drawer`，而非新增路由。
- 列表默认提供卡片 / 表格切换并持久化。
- 流式面板用 keep-alive，避免重拉流。

不确定时查真相源，不要自行发挥。
