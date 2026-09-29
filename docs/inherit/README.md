# WorkMason 交互/布局继承套件

把本套件复制到**目标项目**，即可让该项目的 AI 编码工具（Cursor / Claude Code /
Copilot / opencode / Codex 等）在 vibe coding 时严格遵循 WorkMason 的前端交互与布局规范。

> 本目录只放**模板**，不参与 WorkMason 运行。文件名刻意用 `.template.*`，
> 避免被本仓库的 agent 误读；复制到目标项目时按下方映射改成正式文件名。
>
> **真相源就是本仓库根目录的 [`DESIGN.md`](../../DESIGN.md)**，套件里的引用统一指向
> 目标项目根目录的 `DESIGN.md`（同名拷贝即可）。

---

## 1. 为什么这样做

AI 每次只读它**当次上下文里出现的文件**。所以"遵守"靠三样东西：

1. **入口规则文件**：让 AI 知道"有规范、规范在哪、哪些是硬约束"。
2. **真相源单点**：详细规范只维护一份（根目录 `DESIGN.md`），入口文件只做薄指针，
   避免多处漂移。
3. **可检查的规则**：用祈使句 + 明确触发条件（"当创建 X 时…"）+ 完成自检清单。
   纯描述性文字 AI 会忽略。

> 更严格的继承（资产层/约束层）见第 5 节。

---

## 2. 文件映射（复制时改成的目标路径）

| 套件文件 | 复制到目标项目 | 服务的工具 |
|----------|----------------|------------|
| `DESIGN.md`（取自本仓库根目录，需单独拷贝） | `DESIGN.md`（根目录，同名） | 真相源（所有工具共同引用） |
| `AGENTS.template.md` | `AGENTS.md` | opencode / Codex / 通用 agent 入口 |
| `CLAUDE.template.md` | `CLAUDE.md` | Claude Code |
| `cursor-inherit.template.mdc` | `.cursor/rules/00-inherit.mdc` | Cursor（按 `globs` 自动附着） |
| `copilot-instructions.template.md` | `.github/copilot-instructions.md` | GitHub Copilot |

**单源原则**：只有 `AGENTS.md` 写完整 MUST 清单；`CLAUDE.md` / Cursor / Copilot
只写"读 AGENTS.md + 真相源"的指针 + 各自特性。后续改规则只改一处。

---

## 3. 使用步骤

```text
1. 把本仓库根目录的 DESIGN.md 拷贝到目标项目根目录（同名 DESIGN.md）
2. 拷贝 AGENTS.template.md      → AGENTS.md
3. 拷贝 CLAUDE.template.md      → CLAUDE.md
4. 建 .cursor/rules/，拷贝 cursor-inherit.template.mdc → .cursor/rules/00-inherit.mdc
5. 建 .github/，拷贝 copilot-instructions.template.md → .github/copilot-instructions.md
6. 打开四个入口文件，把 <PROJECT> / <UPSTREAM> 占位符换成实际名称
7. 若目标项目目录结构不同（非 src/pages、src/api…），同步修正路径引用
8. commit，然后让 AI 建一个新页面试跑，检查是否自动套用布局/主题/权限
```

---

## 4. 校验它是否真的生效

- 让 AI「新建一个设置页」，观察它是否：套用 `PageShell`/`MainLayout`、用 `--fn-*`、
  走 i18n、挂权限，而不是自造布局。
- 新建文件后 `git diff` 检查有没有硬编码颜色、裸 `fetch`、裸 `toLocaleString()`。
- 若 AI 漂移：把对应反例补进 `AGENTS.md` 的"禁止项"，并考虑加第 5 节的 lint 门禁。

---

## 5. 想更"严格"时的下一步

约定层（本套件）只解决"知道"。要"强制"，继续：

- **资产层**：把 WorkMason 的 `styles/theme-vars.css`、`context/ThemeContext.tsx`、
  `layouts/`、`api/request.ts`、`utils/permissions.ts` 抽成私有 npm 包
  （pnpm workspace），目标项目直接 import，不再复制。
- **约束层**：
  - ESLint `no-restricted-syntax` 禁裸 `toLocaleString()`、组件内 `fetch`、硬编码色值
  - Stylelint 只允许 `--fn-*` token
  - 单测校验 i18n `en/zh` 键对齐
  - Playwright/Chromatic 视觉回归对比关键页面布局
