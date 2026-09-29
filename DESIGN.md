# WorkMason 前端业务交互设计

> 本文档描述 WorkMason Dashboard（React + TypeScript 单页应用）的**业务交互实现**，
> 作为根目录的参考文件，与 [`AGENTS.md`](./AGENTS.md)（模块边界 / 协作规范）和
> [`docs/0.10.0/architecture.md`](./docs/0.10.0/architecture.md)（后端整体架构）互补。
>
> - 源码位置：`dashboard/`（**唯一可编辑的前端源码目录**）
> - 构建产物：`src/workmason/dashboard/`（由 `make build-frontend` 生成，**禁止直接编辑**）
> - 前端与后端只通过 `/api` HTTP / WebSocket 通信，从不导入 Python 模块、不直连数据库。

---

## 1. 技术栈与构建

| 维度 | 选型 |
|------|------|
| 框架 | React 18 + TypeScript |
| 构建 | Vite 6（`dashboard/vite.config.ts`） |
| 组件库 | Ant Design 5（`prefixCls="workmason"`）+ antd-style |
| 路由 | react-router-dom 7（`BrowserRouter`，`useTransitions={false}`） |
| 状态 | React Context + 模块级外部 store（`useSyncExternalStore`） |
| 国际化 | i18next + react-i18next（`zh` / `en`） |
| 流式 | 原生 WebSocket + `EventSource`(SSE) |
| 富文本 | react-markdown + remark-gfm/math + rehype-katex + mermaid |
| 终端 | @xterm/xterm + addons |
| 编辑器 | @monaco-editor/react |
| 图表 | recharts（用量分析） |
| 表格/虚拟滚动 | antd Table + react-virtuoso |
| PWA | vite-plugin-pwa（`registerType: "prompt"`） |
| 测试 | vitest + @testing-library/react |

构建要点（`vite.config.ts`）：

- 产物输出到 `../src/workmason/dashboard`；`manualChunks` 拆分 `vendor-react` / `vendor-antd` /
  `vendor-xterm` / `vendor-markdown` / `vendor-icons` / `vendor-utils`。
- PWA 采用 `prompt` 模式（不自动刷新），Service Worker 注册由 `src/sw-register.ts` 手动接管；
  `index.html` **不**预缓存，导航请求 NetworkFirst，静态资源 CacheFirst。
- 生产构建 `esbuild` 移除 `console.log/debug/info`；`minifyIdentifiers:false` 规避 `@xterm/xterm` 崩溃。
- 自定义插件 `suppressViteDisconnectReload()` 阻止 HMR 断连触发 `location.reload()`。

---

## 2. 目录结构与分层

```
dashboard/src/
  main.tsx                 入口：PWA 预注册 → initI18n → render → 注册 SW
  App.tsx                  根组件：Router + 主题 + Provider 树 + 路由表
  i18n.ts                  i18next 初始化、语言包懒加载、服务端标签水合
  pwa.ts / pwa-prompt.ts   安装提示捕获、SW 更新
  sw-register.ts           Service Worker 生命周期
  api/                     HTTP/WS 适配层（request、config、modules、types）
  routes/                  路由表、path↔key 映射、全屏路径集合
  layouts/                 应用外壳：MainLayout、Sidebar、Header、PageShell
  context/                 全局 React Context
  hooks/                   通用可复用 hooks
  components/              共享 UI 组件
  pages/                   路由级页面
  plugins/toolRenderers/   工具渲染插件系统
  styles/                  主题变量、全局样式
  locales/                 en.json / zh.json
  utils/                   纯函数工具
```

**分层约定**（与 `AGENTS.md §5` 一致）：

```
dashboard/src/api  ──►  （HTTP /api）  ──►  后端
pages/ ──► components/ + hooks/ + context/ + api/
components/ 不得直接 fetch，必须走 api/ 模块
utils/ 为纯函数，不感知 API / 服务端布局
```

---

## 3. 应用外壳与启动流程

### 3.1 启动顺序（`main.tsx`）

1. **首行** `import "./pwa-prompt"`：同步捕获 `beforeinstallprompt`（Chrome 可能在 React 挂载前触发）。
2. 安装 chunk 失效恢复（`installChunkLoadRecovery`），抑制无害控制台噪声与 `unhandledrejection`。
3. `initI18n()` 完成后再 `createRoot(...).render(<App/>)`。
4. 尽早 `registerSW()`（DEV 下仅清理旧 worker，不注册）。

### 3.2 根组件（`App.tsx`）

```
BrowserRouter
  GlobalErrorBoundary
    ThemeProvider
      ThemedApp
        ConfigProvider (antd theme + locale + prefixCls="workmason")
          AntdAppProvider        # 绑定主题化 message/modal
            DesktopChromeProvider
              Routes
                /login, /login/oidc/complete, /setup, /invite   # 公开路由
                /*  → AuthGuard → AgentProvider → LayoutModeProvider
                              → VoiceOutputProvider → MainLayout
```

- `ThemedApp` 负责 antd token（暗色/亮色 + 品牌色）、`dayjs` locale、文档标题、
  `useUnauthorizedRedirect()`、桌面端外链拦截。
- 路由懒加载 + `Suspense` 兜底（`PageLoading`）。

### 3.3 鉴权与权限守卫

- `components/AuthGuard.tsx`：检查 `/api/setup/status`（未初始化跳 `/setup`）→ 校验 JWT
  → `GET /auth/me` 并应用用户 locale → 注入 `CurrentUserProvider`。后端不可达时 fail-open。
- `components/RequirePermission.tsx`：按路径权限（`pathPermissionKeys`）包裹受限路由，
  无权限渲染 `ForbiddenPage`。
- `api/request.ts` 中的 401 / 503 处理会派发 `WorkMason:unauthorized` /
  `WorkMason:forbidden` 事件，`useUnauthorizedRedirect` 监听后走 SPA 内跳转（保留已加载 chunk）。

---

## 4. 路由与导航

### 4.1 路由表（`routes/index.tsx`）

- `routeConfigs`：全部懒加载页面；`useWrapper` 用于聊天页（`/chat`、`/chat/:agentId`、`/chat/:agentId/:threadId`）。
- `pathToKey`：路径 → 侧边栏选中 key 的映射。
- `FULLSCREEN_PATHS`：占满内容区、无内边距的页面（`/chat`、`/workbench*`、`/remote-desktop*`）。
- 大量旧 URL 通过 `<Navigate>` 重定向（如 `/sessions → /chat`、`/cron-jobs → /tasks`、
  `/channels → /personalization/channels`），保证旧书签可用。
- `resolveSelectedKey()` 处理带参路径（`/chat/...`、`/personalization/...` 等）的高亮归并。

### 4.2 应用外壳（`layouts/MainLayout/index.tsx`）

- 左侧固定 `Sidebar`（可折叠，状态持久化 `WorkMason:sidebar:collapsed`）+ 桌面端边缘拖拽控件 `RailEdgeControl`。
- 聊天路由额外渲染聊天历史 rail 的 portal 挂载点（经典布局），或由侧栏内嵌（极简布局）。
- **Workbench keep-alive**：`workbenchMounted` 一旦挂载就不再卸载，通过 `display` 切换可见性，
  避免离开/返回时重挂载所有懒加载页面（卡顿 + 丢失 UI 状态）。
- 路由区与 Workbench 同样以 `display:none` 保留挂载。
- 移动端：折叠侧栏 + 遮罩；`WorkMason:toggle-nav` 事件允许页面（聊天页移动头部）触发全局导航开关。
- `layoutMode`（`classic` / `minimal`）影响聊天历史与侧栏的呈现方式。

### 4.3 侧边栏导航（`layouts/sidebarNav.tsx`）

- 分组：**主区**（chat / experts / tasks / token-usage）、**设置**（personalization / channels /
  connectors / skill-packages / knowledge-bases）、**控制**（workbench / remote-desktop / acp）、
  **管理**（admin-users / models / admin-storage / admin-plugins / admin-security / admin-advanced）。
- 每个条目按 `navAllowed(user, key)` 做权限过滤，标签走 i18n（`nav.*`）。
- 宽度常量 `EXPANDED_WIDTH=220` / `COLLAPSED_WIDTH=56`。

---

## 5. 全局状态（Context）

| Context | 文件 | 职责 |
|---------|------|------|
| `ThemeProvider` | `context/ThemeContext.tsx` | 亮/暗/跟随系统 + 品牌色板（含自定义色），写入 `<html data-theme/data-palette>` |
| `AgentProvider` | `context/AgentContext.tsx` | 拉取 `/api/agents`、维护 `activeAgentId`、同步 `localStorage` 与 `request.ts` |
| `LayoutModeProvider` | `context/LayoutModeContext.tsx` | `classic` / `minimal` 布局模式与极简下窗格 |
| `VoiceOutputProvider` | `context/VoiceOutputContext.tsx` | TTS 播报状态（消息朗读） |
| `BackupOperationProvider` | `context/BackupOperationContext.tsx` | 备份/恢复跨页操作状态 |
| `ServiceRestartProvider` | `context/ServiceRestartContext.tsx` | 服务重启编排与遮罩 |
| `CurrentUserProvider` | `hooks/useCurrentUser.tsx` | 当前登录用户（AuthGuard 注入） |
| `ScrollContext` | `context/ScrollContext.tsx` | 跨组件滚动位置协调 |

### 5.1 多 Agent 导航（`AgentContext`）

- `WorkMasonAgent` 模型包含 `agent_id`（公开字符串 id）、`state`（running/stopped/…）、
  `is_shared`/`is_owner`、`unread_count`、`bootstrap_pending`、默认模型/知识库/连接器等。
- 选中 agent 会**同步**写入 `request.ts` 模块级变量，使所有 agent 作用域请求带上
  `X-WorkMason-Agent-Id` 头（见 §6.2）。
- `refresh()` 做**内容比对**，无变化时保持引用，避免聊天页 10s 轮询导致的全树重渲染。
- `selectEnabledExperts()` / `projectChatAgentOption()`：聊天侧仅暴露 `running` 的专家，
  供侧栏、`@` 选择器、斜杠菜单复用。

---

## 6. API 通信层

### 6.1 核心封装（`api/request.ts`）

| 导出 | 用途 |
|------|------|
| `request<T>()` | JSON 请求（默认 `Content-Type: application/json`） |
| `requestBlob()` | 二进制下载，支持 `onProgress(loaded,total)` |
| `requestStream()` | 返回 `ReadableStream`（分块 TTS） |
| `requestUpload()` | `FormData` 上传（XHR，支持上传进度） |
| `probeAuthResource()` | 只校验成功、取消 body 的存在性探测 |

统一处理：

- **JWT**：`Authorization: Bearer`；`X-WorkMason-Access-Token` 响应头用于滑动续期（`applyRenewedAccessToken`）。
- **语言**：自动带 `Accept-Language`（zh/en），驱动后端本地化。
- **首次运行锁定**：`503 {setup_required:true}` → 一次性跳转 `/setup`，并短路后续请求。
- **401**：清 token + 派发 `UNAUTHORIZED_EVENT`（可取消，SPA 内跳登录）。
- **403（agent 作用域）**：派发 `FORBIDDEN_EVENT`。
- **URL**：`getApiUrl(path)` 统一加 `/api` 前缀（`api/config.ts`）。

### 6.2 Agent 作用域头

`isAgentScopedPath()` 判定 `/agents/<id>/...` 与 `/mbti/*` 请求，自动注入
`X-WorkMason-Agent-Id`（来源为 `AgentContext` 同步的模块级 `activeAgentId`），
无需在每个调用点手动传递。

### 6.3 模块划分（`api/modules/*`）

按资源拆分（`agent`、`agentChat`、`agentTools`、`channel`、`connectors`、`cronjob`、
`knowledgeBases`、`skillPackages`、`publishedExperts`、`expertMarket`、`memory*`、
`provider`、`settings`、`security`、`browser`、`desktop`、`mobile`、`terminalAi`、
`voice`、`embedding`、`observability`、`backup`、`tls`、`sso`、`invites`、`plugins`、
`workspace`、`workmasonThreads`、`workmasonAgents`、`wsChat`、`wsNotifications` 等）。

`api/index.ts` 汇聚为 `api` 对象并重导出 `request` 与类型；`api/types/*` 定义领域类型。

### 6.4 错误模型

后端返回 `{ error: { code, message, details } }`：

- `utils/apiError.ts` 的 `parseApiError` / `apiErrorMessage` 提取并优先按 `apiErrors.<CODE>`
  本地化，附带 `details.reason`。
- Dashboard 的 `locales/{en,zh}.json → apiErrors.*` 与后端 `i18n` 的 `errors.*` 键需保持同步
  （由 `tests/unit/i18n/` 校验）。

---

## 7. 实时通信

| 通道 | 端点 | 实现 |
|------|------|------|
| 聊天流 | `WS /api/agents/{aid}/chat/ws` | `api/modules/wsChat.ts` + `pages/Chat/hooks/chatStore.ts` |
| 主动推送通知 | `WS /ws-notifications` | `hooks/useDashboardPushToast.tsx` |
| 浏览器流 | `WS /browser-stream/ws` | `hooks/useBrowserSessionState.ts`、`useBrowserStream.ts` |
| 终端 | WS | `pages/Control/Terminal` + `hooks` |
| 远程桌面 / 手机 | WS（canvas / H.264 WebCodecs） | `useDesktopStream`、`useMobileStream` |
| HITL 恢复 | `POST .../chat/hitl/resume`（SSE） | `chatStore.resumeHitl` + `consumeSseResponse` |
| 运行轨迹 | `EventSource` SSE | `pages/Chat/hooks/useTrajectorySession.ts` |

**通知推送**（`useDashboardPushToast`）：指数退避重连（上限 15s）、25s ping；
收到推送后静默刷新 agents、`invalidateHistory(thread_id)`、派发 `sessionsChanged`，
并弹出可点击跳转到对应会话的右下角通知。

---

## 8. 聊天核心交互（业务交互的主线）

### 8.1 模块级 store（`pages/Chat/hooks/chatStore.ts`）

设计要点：**WebSocket 流与累积消息存活在 React 之外**，组件通过订阅获取快照，
因此离开页面再返回时消息（含进行中的流式内容）仍然保留。

- 状态结构：`Map<sessionId, SessionStreamState>`（`sseHelpers.ts` 定义类型）。
- 对外 API：`subscribe` / `getSnapshot` / `setMessages` / `setHistoryPage` /
  `prependHistoryMessages` / `appendUserMessage` / `clearMessages` / `renameSessionKey` /
  `sendTurn` / `attachThread` / `cancelStream` / `resumeHitl` / `patchToolResultData` 等。
- 事件桥：`onStreamEvent` / `onToolEvent` / `onSessionEvent` / `onSlashAction`，
  供聊天页、侧栏、消息列表解耦联动。
- **临时会话键**：新会话先用 `__pending__`，创建成功后 `renameSessionKey` 迁移到真实
  `thr_*`（连同 live socket、续流簿记、焦点），避免跨线程串流。

### 8.2 流式协议处理

`handleHarnessChunk` 将 harness 分块映射为 UI 状态：

| chunk | 行为 |
|-------|------|
| `token` | 追加到当前流式 assistant 气泡（必要时新建气泡） |
| `reasoning` | 追加为 `thinking` 内容块（可折叠思考面板） |
| `usage` | 按 `call_id` 采样累加（重连时替换而非重复计数） |
| `tool_call_chunk` | 按 harness `id`（回退 `index`）upsert 工具气泡 |
| `tool_result` | 关闭对应工具气泡，解析错误/媒体 |
| `done` / `error` / `hitl_required` | 收尾流式、追加错误气泡或 HITL 卡片 |
| `attachment` | 挂到最近工具/助手消息 |
| `state_update` / `state_snapshot` | 更新上下文 token 用量 |
| `slash_action` | 派发斜杠动作（如 `switch_agent`） |

### 8.3 发送链路

```
ChatInput.submitMessage
  → wrappedHandleSend (Chat/index.tsx)      # 技能录制拦截 → HITL 路由 → 队列
    → useChatSend.handleSend                # 解析技能/@/模型，创建会话，构造 composerContext
      → useChat.sendMessage                 # 追加用户消息
        → chatStore.sendTurn                # 打开 WS，发送 user_turn
```

- **新会话**：先落 `__pending__` 并导航，待线程创建成功后 `renameSessionKey` + `sendTurn`。
- **编辑重发**：`truncateAndReplaceUserMessage` 截断历史并原地替换后重发。
- **重新生成**：找到该 assistant 消息之前的用户消息，重新发送。
- **分叉（fork）**：`workmasonThreadsApi.fork`（按 `assistant_turns_from_end`）后导航到新线程。

### 8.4 弱网续流与自愈（`wsResumeGate.ts` + `chatStore`）

- 意外断连 → `attachThread` 重订阅（上限 `MAX_STREAM_RESUME_ATTEMPTS`）；
  后台标签页断连先记录 `pendingResumeBySession`，回到前台再重绑。
- `turn_status.active` 判断服务端是否仍在跑；`streamResume` 事件提示"已恢复"。
- **看门狗**：`isStreaming` 粘滞但无 socket 超过 `STREAM_STALE_WITHOUT_SOCKET_MS` 时强制封口，
  恢复上滑刷新 / 自由滚动 UX。
- 底部**过度滚动刷新**：`refreshHistory` 拉取最新页并与本地合并（保留已加载的旧页）。

### 8.5 消息列表与滚动（`components/MessageList.tsx` + `hooks/useAutoScroll.ts`）

- **分组**：连续 assistant/tool 行合并为一个"轮次"（`groupConsecutiveAssistantMessages`）。
- **虚拟滚动**：≥30 组启用 react-virtuoso，`firstItemIndex=1_000_000` 支持向前插入，
  一旦启用即锁定（`useVirtualLocked`），切换会话重置。
- **两种滚动模式**（`useAutoScroll`）：`follow`（自动贴底）与 `free`（显示"回到底部"按钮、不自动移动）；
  通过 `ResizeObserver` + `MutationObserver` + 程序化滚动守卫协调。
- **加载更早**：`loadOlderGate.ts` 控制触发/上锁/自动填充；插入后按组数差修正滚动锚点。
- 底部渲染 `GeneratingIndicator`（含耗时与取消），轮次左侧渲染 `TurnTimelineRail`。

### 8.6 助手轮次渲染

- `AssistantTurnView`：将轮次拆为"过程步骤（thinking + tool）"与"最终回答"，顺序渲染
  待办面板 → HITL 分段 → 过程折叠 → 工具媒体条 → 回答气泡 → 知识引用 → 打开浏览器/已改文件入口。
- `AssistantProcessSummary`：流式时自动展开、结束后自动折叠（手动切换保持到下次状态迁移）。
- `MessageBubble`：用户气泡支持行内编辑、图片/视频/音频/文件；助手气泡支持错误调试信息、
  复制、朗读、重新生成、分叉，并展示轮次 token 用量（服务端时区时间戳）。

### 8.7 人类在环（HITL）

- `HitlApprovalCard`：逐动作摘要 + 批准/拒绝；已决状态显示徽章。
- `AskQuestionCard`：多问题分步向导，支持单选/多选/自由输入/复核/跳过。
- 待处理的 ask 卡片会**提升到输入框上方**（不重复出现在正文），普通待决 HITL 阻止开启新轮次；
  纯文本发送在存在 ask 暂停时转为 `resumeHitl(respond)`。

### 8.8 输入框（`components/ChatInput.tsx` + `ChatInputActionsRow.tsx`）

- 草稿按 `agentId:threadId` 存 `sessionStorage`（防抖 250ms）。
- 预填充通过命令式 `setPrefillText` 注入（避免父组件重渲染打断编辑）。
- 能力选择器：模型（含推理模式/强度）、连接器、知识库、技能、专家、子代理；
  桌面用 Popover，移动用 Drawer，窄屏用溢出菜单（`ResizeObserver` ≤560px 判定紧凑）。
- 斜杠命令 + `@` 提及（连接器/专家/子代理/工作区文件）统一由 `useSlashMentionInput` 处理键盘导航。
- 附件（图片/视频/音频/文件）经 `useChatAttachments`；语音输入 `useVoiceInput`。
- **消息队列**：流式期间发送进入 FIFO 队列（上限 10，按线程键隔离），流结束后自动 flush；
  HITL 暂停时延迟 flush，失败重排。
- 状态区：上下文窗口环 `ContextWindowRing`、新建会话、润色、语音、浏览器录制、
  发送/入队/停止按钮。

### 8.9 Dock 面板系统（`hooks/useChatDockPanel.ts` + `ChatDockPanels.tsx`）

- Tab 类型：`files | browser | terminal | file | knowledge | toolUi`；模式 `bottom`/`right`/`popup`（移动强制 bottom）。
- **Keep-alive**：一旦存在 tab 面板即保持挂载，切换仅切 `display`，重组件（Monaco / xterm / 浏览器 iframe）不卸载。
- 尺寸/模式持久化（`WorkMason:chat-dock:*`，含旧键迁移）；拖拽期间直接写 DOM，pointerup 才提交状态。
- 关闭 dock 会丢弃全部 `toolUi` tab，使其回到消息流内渲染。
- 内容面板：文件树、单文件预览/编辑、知识引用、浏览器工作区、终端、工具 UI。

### 8.10 运行轨迹（Trajectory）

- `TrajectoryDrawer` + `useTrajectorySession`：初始历史页 + SSE 增量（按 `event_id` upsert，
  断线退避重连，`beforeSeq` 向后翻页）。
- 三泳道时间轴（input/model/tools）支持滚轮缩放、右键平移、左键框选时间范围、点击选中。
- 台账（Ledger）+ 检查器（Inspector：Summary/Payload/Preview/Raw），支持导出 JSONL。

---

## 9. 工具渲染插件系统（`plugins/toolRenderers/`）

面向第三方插件与内置富 UI 的统一渲染管线：

- **信封**：工具返回 JSON 可携带 `workmason_ui: { renderer, version? }` 与 `data`。
- **解析**：`parseToolOutput.ts` 提取 `workmason_ui`/`data`/`text`；`mergePatchedToolOutput`
  支持 `patchResult` 原地更新 data（无需重跑 LLM）。
- **注册表**：`registry.ts` 维护 `pluginId::id` 与 `tool_name` 两个索引 + 版本订阅。
  解析顺序：`workmason_ui.renderer`（精确/任意匹配）→ 工具名映射。
- **内置**：`ensureBuiltins.ts` 暴露 `window.__WORKMASON_REACT__/JSX__` 并注册默认渲染器；
  未注册但有 `workmason_ui` 时渲染 `BuiltinWorkMasonUiFallback`。
- **宿主**：`host.ts` 提供给插件的 API（`registerRenderer` / `getToolContext` / `patchResult` /
  `request` / `openSidePanel` / `closeSidePanel`）。
- **加载**：`loader.ts` 以带鉴权的 fetch + Blob URL `import()` 插件 ESM；`usePluginToolUis`
  在会话内一次性加载，`useToolRendererVersion` 驱动重渲染。
- **隔离**：`ToolUiErrorBoundary` 捕获坏插件并回退；富 UI 可"钉"在消息流外
  （`isPinnedToolUiMessage` / `partitionPinnedTools`）或停靠到侧面板。

---

## 10. 主题与样式

- **两个正交轴**：模式（light/dark/system）× 品牌色板（8 个预设 + custom）。
- 单一数据源在 `context/ThemeContext.tsx`；`styles/` 仅存放纯数据与 CSS。
- DOM 标记：`<html data-theme="light|dark" data-palette="<key>">`；同时同步
  `<meta name="theme-color">` 以匹配 PWA 浏览器 chrome。
- **CSS 变量优先**：`styles/theme-vars.css` 以 `--fn-*` 定义背景/文本/边框/品牌/阴影/圆角等
  token（亮/暗 + 各色板覆盖块）；自定义色板在运行时注入
  `<style id="WorkMason-custom-palette">`（`customPaletteCssVars` 含 WCAG 对比度校正）。
- **antd token 叠加**：`App.tsx` 用 `brandTokensFor()` 生成 `themeConfig`，
  `<ConfigProvider prefixCls="workmason">`；暗色下额外在 `layout.css` 强制组件背景。
- 全局样式：`layout.css`（重置/页面布局/暗色覆盖/移动端/滚动条）、`form-override.css`、
  `spin-override.css`。
- 持久化：`styles/appearanceStorage.ts`（JSON 单键 `theme`，兼容旧字符串键与旧色板键）。

---

## 11. 国际化（i18n）

- 仅支持 `zh` / `en`，`en` 为回退。
- 初始化（`i18n.ts`）：按浏览器语言/用户偏好加载主语言包，空闲时预取回退包；
  `languageChanged` 时同步 `<html lang>` 并水合服务端标签。
- **服务端文案水合**：`GET /api/i18n/tools`、`/api/i18n/skills` 合并到 `tools.*` / `skills.*`。
- 语言切换（`pages/Settings/Language`）走 `preferencesApi.setLocale` + `applyUserLocale`。
- Dashboard 文案在 `locales/{en,zh}.json`；服务端拥有的文案（工具名、`apiErrors`）
  以后端 `src/workmason/i18n/*.json` 为准并保持同步（`tests/unit/i18n/` 校验键一致）。
- **时区**：面向用户的时间显示一律用服务端时区（`useServerTimezone` +
  `utils/formatMessageTime.ts` 的 `formatServer*`），禁止裸 `toLocaleString()`。

---

## 12. 权限模型

`utils/permissions.ts` 镜像后端 `user_has_permission`：

- `PERM`：模块 key 组合（如 `workbench: ["browser","terminal"]`、`acp: "admin"`）。
- `NAV_PERMISSIONS`：侧边栏 key → 权限；`*_TAB_PERMISSIONS`：页面 tab → 权限。
- `userCan` / `userCanAny` / `navAllowed`（admin 直通）；`pathPermissionKeys` 为路径权限表；
  `canAccessPath` 供 `RequirePermission` 使用。
- 页面内 tab 级过滤用 `hooks/useGatedSearchTabs.ts`（`?tab=` 与权限联动）。

---

## 13. 业务页面清单

### 13.1 认证 / 初始化

| 页面 | 关键交互 |
|------|----------|
| `Login` | 账号密码 + 验证码（滑块/Turnstile/hCaptcha/reCAPTCHA/腾讯，适配器模式）+ SSO（OIDC 跳转，其余弹窗 + postMessage） |
| `Login/OidcComplete` | 从 URL fragment 优先读参、`safeRedirect` 防开放重定向、剥离地址栏凭证、换 token |
| `Setup` | 首次运行向导，可恢复；步骤 密码 → 数据库（SQLite/PG beta）→ 管理员 → 模型（预设/自定义，须测试通过）→ 完成；短 TTL 向导 token 隔离 |
| `Invite` | 邀请码三步：校验 → 建账号 → 完成 |

### 13.2 专家 / 市场（`pages/Experts/`）

- 三 tab：**我的专家**（卡片/表格，启动/停止/编辑/删除/发布）、**专家库**（内置模板 + 已发布）、
  **专家市场**（SkillHub）。
- 统一创建抽屉 `CreateFromExpertDrawer`：来源 `builtin|published|market`，头像/配色、默认模型、
  后端（根目录探测 + bubblewrap 引导）、技能包、编辑器默认项、模板文件预览。

### 13.3 Agent 管理（`pages/Agent/`）

| 区域 | 说明 |
|------|------|
| `Personalization` | 路径 tab 中枢：skills / subagents / tools / plugins / mbti / memory / channels，keep-alive |
| `Config` | 运行时限制与高级配置（`PATCH /agents/{id}`） |
| `Channels` | 每 agent IM 绑定；`ChannelDrawer` 扫码绑定状态机（自动保存 + TTL 防重） |
| `Connectors` | 全局连接器目录 + 实例；OAuth 弹窗、CLI 安装、设备流、表单草稿 |
| `Skills` | 自定义 / 内置 / SkillHub / 技能包；导入 URL/ZIP |
| `Tools` | 内置 / ACP / 插件；乐观切换 + 回滚 |
| `Memory` | 9 个 tab：overview/profile/library/episodes/candidates/journal/conversations/proactive/settings |
| `ACP` | 运行器注册与每 agent 启用 |
| `Workspace` | 抽屉式文件浏览器：懒加载树、拖拽移动、上传/下载、归档导入导出、编辑器/预览 |

### 13.4 控制（`pages/Control/`）

- `CronJobs`（Tasks）：定时任务表格/卡片、抽屉编辑、立即执行、历史。
- `Workbench`：浏览器 + 终端路径 tab，keep-alive。
- `Terminal`：多会话 xterm over WS + AI 面板（命令执行/自动驾驶）。
- `RemoteBrowser`：远程 Chromium，安装流、书签、视口预设、AI 面板、技能录制。
- `RemoteDesktop` / `RemoteAndroid`：远程桌面/手机，canvas 指针键盘映射、画质预设、ADB shell。
- `TokenUsage`：用量分析（recharts），维度视图 + Excel 导出。

### 13.5 设置（`pages/Settings/`）

`Models`（chat/generation/voice/search）、`Security`（hitl/filesystem/pii/tool_guard/skill_scan/audit）、
`AdvancedSettings`（env-vars/observability/backup/https/captcha/updates）、`BackupRestore`、
`Embedding`（本地 ONNX 下载进度）、`Environments`、`HttpsSettings`（Let's Encrypt 向导）、
`Language`、`MediaGeneration`、`Observability`（Langfuse）、`SearchConfig`、`Voice`。

### 13.6 管理（`pages/Admin/`）

`Users`（local + SSO tab，邀请码、角色、权限目录、配额）、`Storage`（存储后端 + 浏览抽屉）、
`Plugins`（安装/上传/重载/启用）、`SharedModels`。

### 13.7 知识库 / 技能包

- `KnowledgeBases`：主从布局；文档上传/索引轮询、文件夹、预览（大纲/查找/全屏）、
  嵌入与 OCR 能力配置、深链 `?kb=&doc=`。
- `SkillPackages`：全局技能包；导入 URL/ZIP、从 Hub 生成技能集、权限感知的写操作。

---

## 14. PWA 与离线

- `pwa-prompt.ts`：模块加载即捕获 `beforeinstallprompt`，提供订阅/快照/触发安装 API。
- `sw-register.ts`：生产注册 `/sw.js`（`updateViaCache:"none"`）；**不做** `controllerchange → reload`；
  等待中的 worker 派发 `pwa:update-ready`；`applyUpdate()` 手动 `SKIP_WAITING` 后单次刷新；DEV 仅清理。
- `PwaUpdatePrompt` / `PwaInstallPrompt`：更新横幅与安装引导（含 iOS 添加到主屏说明）。
- 缓存策略：导航 NetworkFirst（3s 超时）、`/assets/*` CacheFirst、`/api/(chats|agent/files|agent/memory)`
  NetworkFirst、字体 CacheFirst。

---

## 15. 跨领域交互约定

| 约定 | 位置 |
|------|------|
| **抽屉优先**：实体 CRUD 多用 antd Drawer 而非独立路由 | Connectors/Channels/Skills/ACP/Voice/Storage/Invite… |
| **卡片/表格视图切换**，持久化 | `useCardTableView` + localStorage |
| **Keep-alive tab**：`display:none` 保留挂载，避免重新拉流 | Personalization / Workbench / RemoteDesktop / Dock |
| **乐观更新 + 回滚** | Tools / Agents / CronJobs / KB 文档 |
| **外部 store**：模块级状态 + `useSyncExternalStore` | `chatStore`、侧栏开关、插件注册表 |
| **命令式预填充**：避免父重渲染打断编辑 | `ChatInputHandle.setPrefillText` |
| **服务端时区** | `useServerTimezone` + `formatServer*` |
| **权限门控** | `RequirePermission` / `navAllowed` / `useGatedSearchTabs` |
| **i18n 全覆盖**：组件内不硬编码用户可见英文 | `t()` 键 |
| **失败自愈**：stale chunk 软刷新、WS 退避重连、错误边界回退 | `reloadOnStaleChunk`、`ErrorBoundary`、`wsResumeGate` |

---

## 16. 快速索引

| 想了解 | 位置 |
|--------|------|
| 应用启动与 Provider 树 | `dashboard/src/main.tsx`、`App.tsx` |
| 路由表与全屏路径 | `dashboard/src/routes/index.tsx` |
| 应用外壳 / 侧栏 / keep-alive | `dashboard/src/layouts/MainLayout/index.tsx`、`sidebarNav.tsx` |
| HTTP 封装与鉴权 | `dashboard/src/api/request.ts`、`api/config.ts` |
| 聊天 store 与流式协议 | `dashboard/src/pages/Chat/hooks/chatStore.ts`、`sseHelpers.ts`、`wsResumeGate.ts` |
| 聊天历史与分页 | `dashboard/src/pages/Chat/hooks/useChat.ts` |
| 发送链路 | `useChatSend.ts`、`pages/Chat/index.tsx` |
| 消息渲染 | `pages/Chat/components/MessageList.tsx`、`AssistantTurnView.tsx`、`MessageBubble.tsx` |
| 输入框与选择器 | `pages/Chat/components/ChatInput*.tsx`、`hooks/useSlashMentionInput.ts` |
| Dock 面板 | `pages/Chat/hooks/useChatDockPanel.ts`、`components/ChatDockPanels.tsx` |
| HITL | `components/HitlApprovalCard.tsx`、`AskQuestionCard.tsx`、`utils/pendingHitl.ts` |
| 轨迹 | `pages/Chat/components/Trajectory*.tsx`、`hooks/useTrajectorySession.ts` |
| 工具渲染插件 | `dashboard/src/plugins/toolRenderers/` |
| 主题与样式 | `dashboard/src/context/ThemeContext.tsx`、`styles/` |
| i18n | `dashboard/src/i18n.ts`、`locales/`、`utils/locale*.ts` |
| 权限 | `dashboard/src/utils/permissions.ts` |
| PWA | `dashboard/src/pwa*.ts`、`sw-register.ts`、`vite.config.ts` |
| 后端整体架构 | `docs/0.10.0/architecture.md` |
| 模块边界与协作规范 | `AGENTS.md` |

---

## 17. 维护须知

- 只编辑 `dashboard/`，UI 改动后运行 `make build-frontend` 重新生成 `src/workmason/dashboard/`。
- 提交前跑 `make all`（`format-all` + `lint` + `typecheck` + `test`）；UI 改动额外
  `cd dashboard && npx tsc -b`。
- 新增 i18n 键须同时更新 `locales/{en,zh}.json`（及后端对应键），并跑
  `uv run pytest tests/unit/i18n -q`。
- 时间显示统一使用服务端时区；不新增 `config.json` 语言键。
- 测试与源码同目录（`*.test.ts(x)`），是各机制的可执行规格说明。
