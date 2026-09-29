# <PROJECT> — Agent 协作规范（前端交互与布局继承）

> 本文件是 AI 编码工具的入口规则。**真相源**：`DESIGN.md`
> （继承自 <UPSTREAM> 的 DESIGN.md）。本文件与真相源冲突时，以真相源为准并更新本文件。

## 0. 适用范围

所有前端改动（`src/**`）。后端 / CLI 规则另见对应章节。

## 1. 动手前必读（MUST）

- 编辑 `src/pages/**`、`src/components/**`、`src/layouts/**`、`src/styles/**`、
  `src/api/**` 前，先读 `DESIGN.md` 的对应章节。
- 不确定某个交互怎么实现时，**先查真相源，不要自行发挥**；查不到就停下来问，不要猜。

## 2. 硬性约束（MUST，违反即返工）

- 颜色 / 圆角 / 间距 / 阴影一律用 `--fn-*` CSS 变量，禁止硬编码 `#hex` / `rgb()` / 魔法 px。
- 组件禁止直接 `fetch` / 开 `WebSocket`，一律走 `src/api/` 模块。
- 用户可见文案必须走 i18n（`t()`），`en` / `zh` 键必须同时存在且树形对齐。
- 面向用户的时间显示必须用服务端时区（`formatServer*`），禁止裸 `toLocaleString()` /
  `toLocaleDateString()` / `toLocaleTimeString()`。
- 路由权限必须走 `permissions.ts` 的 `userCan` / `RequirePermission`，不得自建判断。
- API 错误必须走 `parseApiError` / `apiErrorMessage`，不得直接展示原始 message。

## 3. 编辑触发规则（When → Do）

- 新建页面：套用 `layouts/PageShell` + `MainLayout` 结构，并把路由注册到 `routes/index.tsx`。
- 新建实体 CRUD：优先用 antd `Drawer`（与既有页面一致），而不是新增路由。
- 新建列表：默认提供卡片 / 表格视图切换，并用 localStorage 持久化选择。
- 新建重度运行面板（浏览器 / 终端 / 桌面 / 流）：使用 keep-alive（`display:none` 切换），
  避免离开返回时重新拉流。
- 新建表单：复用共享字段组件与 `ResizableTable` / `Skeleton` / `EmptyState`。

## 4. 照抄范式（不要重新发明）

- 布局外壳：`src/layouts/MainLayout/index.tsx`
- 主题 / 品牌色：`src/context/ThemeContext.tsx` + `src/styles/theme-vars.css`
- HTTP / 鉴权封装：`src/api/request.ts`
- 权限：`src/utils/permissions.ts`

## 5. 禁止项

- 禁止直接编辑构建产物目录（只编辑前端源码目录，构建另行执行）。
- 禁止在组件内硬编码服务端文案。
- 禁止绕过设计系统引入第二套主题或组件库。

## 6. 完成前自检

- [ ] typecheck / lint / test 全绿
- [ ] 无新增硬编码颜色或裸时间格式化
- [ ] i18n `en` / `zh` 键对齐
- [ ] 交互与 `DESIGN.md` 描述一致
