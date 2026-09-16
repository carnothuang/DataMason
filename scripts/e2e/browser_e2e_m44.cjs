/* M4-4 真浏览器 E2E（Playwright / Chromium）：AI 生成历史治理。
 * 验收线：先制造生成记录（部署提案 + 问数据）→ 设置页「AI 生成」Tab 显示记录与统计、按类型筛选生效
 *        → 工作台工程室「AI 生成」显示本部署历史。
 * 前置：scripts/e2e/serve_for_e2e.py 已在 127.0.0.1:8765 运行、托管 web/dist，
 *        已预置演示部署「客户与订单E2E」；LLM 走桩（确定性）。
 * 运行：python scripts/e2e/run_browser_e2e.py browser_e2e_m44.cjs
 * 环境变量：DATAMASON_E2E_USER / DATAMASON_E2E_PASS（默认 laomao / pw）
 */
const { chromium } = require("playwright");

const BASE = process.env.E2E_BASE || "http://127.0.0.1:8765";
const LIMIT_MS = 180_000;
const SHOT_DIR = process.env.E2E_SHOT_DIR || ".workbuddy/tmp";
const HEADLESS = process.env.E2E_HEADED !== "1";
const E2E_USER = process.env.DATAMASON_E2E_USER || "laomao";
const E2E_PASS = process.env.DATAMASON_E2E_PASS || "pw";

const log = (...a) => console.log("[e2e]", ...a);

async function shot(page, name) {
  const p = `${SHOT_DIR}/e2e_m44_${name}.png`;
  await page.screenshot({ path: p, fullPage: true }).catch(() => {});
  log("screenshot:", p);
}

async function api(page, path, options = {}) {
  return page.evaluate(
    async ({ baseUrl, p, o }) => {
      const r = await fetch(baseUrl + "/api/v1" + p, {
        method: o.method || "GET",
        headers: o.body ? { "Content-Type": "application/json" } : undefined,
        body: o.body ? JSON.stringify(o.body) : undefined,
        signal: AbortSignal.timeout(30000),
      });
      let data = null;
      try { data = await r.json(); } catch { /* ignore */ }
      return { status: r.status, data };
    },
    { baseUrl: BASE, p: path, o: options },
  );
}

async function login(page, user, pass) {
  await page.goto(BASE + "/", { waitUntil: "networkidle" });
  if ((await page.getByRole("button", { name: /登\s*录/ }).count()) > 0) {
    await page.getByPlaceholder("用户名").fill(user);
    await page.getByPlaceholder("密码").fill(pass);
    await page.getByRole("button", { name: /登\s*录/ }).click();
  }
  await page.getByText("我部署过的系统").first().waitFor({ timeout: 15000 });
}

(async () => {
  const browser = await chromium.launch({ headless: HEADLESS, args: ["--no-proxy-server"] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
  page.on("console", (m) => {
    if (m.type() === "error") log("console.error:", m.text().slice(0, 300));
  });

  const failures = [];
  const t0 = Date.now();
  const elapsed = () => ((Date.now() - t0) / 1000).toFixed(1) + "s";
  const rowCount = async (scope) =>
    page.locator(`${scope} .ant-table-tbody tr.ant-table-row`).count();

  try {
    await login(page, E2E_USER, E2E_PASS);
    log("已登录", elapsed());

    const dep = await page.evaluate(async (base) => {
      const r = await fetch(base + "/api/v1/deployments");
      const body = await r.json();
      const d = Array.isArray(body)
        ? body.find((x) => String(x.biz_name).startsWith("客户与订单E2E")) : null;
      return d ? { entry_path: d.entry_path, id: d.id } : { error: "未找到演示部署" };
    }, BASE);
    if (!dep?.entry_path) throw new Error("取部署失败: " + (dep?.error || "未知"));

    // 制造生成记录：部署提案（deploy）+ 问数据（qa，带 deployment_id）
    const pv = await api(page, "/deployments/preview", { method: "POST", body: { description: "留言板" } });
    if (pv.status !== 200) throw new Error("preview 失败: " + pv.status + " " + JSON.stringify(pv.data));
    const qa = await api(page, `/app/${dep.id}/qa/ask`, { method: "POST", body: { question: "按分类统计客户" } });
    if (qa.status !== 200) throw new Error("qa 失败: " + qa.status + " " + JSON.stringify(qa.data));
    log("已制造 deploy + qa 生成记录", elapsed());

    // 1. 设置页「AI 生成」Tab
    await page.goto(BASE + "/settings", { waitUntil: "networkidle" });
    await page.getByRole("tab", { name: "AI 生成" }).click();
    await page.locator(".ant-tabs-tabpane-active .ant-table-tbody").waitFor({ state: "attached", timeout: 15000 });
    await page.waitForTimeout(400);
    const allRows = await rowCount(".ant-tabs-tabpane-active");
    if (allRows < 2) failures.push(`AI 生成 Tab 记录数偏少：${allRows}`);
    const tabText = await page.locator(".ant-tabs-tabpane-active").innerText();
    if (!tabText.includes("问数据")) failures.push("AI 生成 Tab 未见「问数据」记录");
    log(`设置页 AI 生成：${allRows} 条记录`, elapsed());
    await shot(page, "01_settings_tab");

    // 2. 按类型筛选 → 问数据
    await page.locator(".ant-tabs-tabpane-active .ant-select").first().click();
    await page.locator(".ant-select-item-option", { hasText: "问数据" }).first().click();
    await page.waitForTimeout(600);
    const filteredText = await page.locator(".ant-tabs-tabpane-active").innerText();
    if (!filteredText.includes("问数据")) failures.push("按类型筛选「问数据」后无记录");
    if (filteredText.includes("部署提案")) failures.push("按类型筛选「问数据」后仍出现「部署提案」");
    log("筛选「问数据」生效", elapsed());
    await shot(page, "02_settings_filtered");

    // 3. 工作台工程室「AI 生成」显示本部署历史
    await page.goto(BASE + dep.entry_path, { waitUntil: "networkidle" });
    const aiMenu = page.locator(".ant-menu-item", { hasText: "AI 生成" }).first();
    await aiMenu.waitFor({ timeout: 15000 });
    await aiMenu.click();
    await page.locator(".ant-layout-content .ant-table-tbody").waitFor({ state: "attached", timeout: 15000 });
    await page.waitForTimeout(400);
    const wsText = await page.locator(".ant-layout-content").innerText();
    if (!wsText.includes("问数据")) failures.push("工作台 AI 生成页未见本部署「问数据」记录");
    log("工作台工程室 AI 生成：本部署历史可见", elapsed());
    await shot(page, "03_workspace_history");
  } catch (e) {
    const dump = await page.evaluate(() => ({ text: document.body.innerText.slice(0, 600) })).catch(() => ({}));
    failures.push("异常: " + (e && e.message ? e.message : String(e)));
    failures.push("页面状态: " + JSON.stringify(dump));
    await shot(page, "99_error");
  } finally {
    await browser.close();
  }

  const total = Date.now() - t0;
  log("总耗时", (total / 1000).toFixed(1) + "s", `限额 ${LIMIT_MS / 1000}s`);
  if (total > LIMIT_MS) failures.push(`总耗时 ${total}ms 超过限额`);

  if (failures.length) {
    console.error("BROWSER_E2E_M44_FAIL");
    for (const f of failures) console.error(" -", f);
    process.exit(1);
  }
  console.log("BROWSER_E2E_M44_OK");
  process.exit(0);
})();
