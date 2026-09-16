/* M4-3 真浏览器 E2E（Playwright / Chromium）：钉住报表（dashboard 页）。
 * 验收线：工作台「问数据」→ 问出视图卡 → 「钉住」→ 左侧菜单出现报表 → 打开报表重跑出图表/数据
 *        → 再次进入问数据抽屉 → 「已钉住的报表」→ 取消钉住 → 菜单与页面齐清。
 * 前置：scripts/e2e/serve_for_e2e.py 已在 127.0.0.1:8765 运行、托管 web/dist，
 *        已预置演示部署「客户与订单E2E」；LLM 走桩（确定性 SQL/图表）。
 * 运行：python scripts/e2e/run_browser_e2e.py browser_e2e_m43.cjs
 * 环境变量：DATAMASON_E2E_USER / DATAMASON_E2E_PASS（默认 laomao / pw）
 */
const { chromium } = require("playwright");

const BASE = process.env.E2E_BASE || "http://127.0.0.1:8765";
const LIMIT_MS = 180_000;
const STAMP = String(Date.now() % 1_000_000);
const SHOT_DIR = process.env.E2E_SHOT_DIR || ".workbuddy/tmp";
const HEADLESS = process.env.E2E_HEADED !== "1";
const E2E_USER = process.env.DATAMASON_E2E_USER || "laomao";
const E2E_PASS = process.env.DATAMASON_E2E_PASS || "pw";

const log = (...a) => console.log("[e2e]", ...a);
const btn = (label) => new RegExp(label.split("").join("\\s*"));

async function shot(page, name) {
  const p = `${SHOT_DIR}/e2e_m43_${name}.png`;
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
        signal: AbortSignal.timeout(25000),
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
  const menuCount = async (text) =>
    page.locator(".ant-menu-item", { hasText: text }).count();

  try {
    await login(page, E2E_USER, E2E_PASS);
    log("已登录", elapsed());

    const dep = await page.evaluate(async (base) => {
      const r = await fetch(base + "/api/v1/deployments");
      const body = await r.json();
      if (!Array.isArray(body)) return { error: `status=${r.status}` };
      const d = body.find((x) => String(x.biz_name).startsWith("客户与订单E2E"));
      return d ? { entry_path: d.entry_path, id: d.id } : { error: "未找到演示部署" };
    }, BASE);
    if (!dep?.entry_path) throw new Error("取部署失败: " + (dep?.error || "未知"));

    // 清理历史钉住（保证可重跑）
    const existing = await api(page, `/deployments/${dep.id}/qa/pins`);
    for (const p of existing.data?.pins || []) {
      await api(page, `/deployments/${dep.id}/qa/pins/${p.page_id}`, { method: "DELETE" });
    }

    // 进工作台（定位到「客户与订单E2E」卡片上的按钮，避免命中其它部署）
    const demoCard = page.locator(".ant-card", { hasText: "客户与订单E2E" }).first();
    const openBtn = demoCard.getByRole("button", { name: btn("打开工作台") }).first();
    await openBtn.waitFor({ timeout: 15000 });
    await openBtn.click();
    await page.getByRole("button", { name: btn("问数据") }).first().waitFor({ timeout: 15000 });
    log("工作台就绪", elapsed());

    // 1. 问出视图卡 → 钉住
    await page.getByRole("button", { name: btn("问数据") }).first().click();
    await page.locator(".ant-drawer input.ant-input").waitFor({ timeout: 10000 });
    await page.locator(".ant-drawer input.ant-input").fill("按分类统计客户");
    await page.getByRole("button", { name: btn("问一句") }).click();
    await page.locator(".ant-drawer canvas").first().waitFor({ timeout: 20000 });
    await page.getByRole("button", { name: btn("钉住") }).first().click();
    await page.locator(".ant-drawer").getByText("已钉住").first().waitFor({ timeout: 10000 });
    log("视图卡已钉住", elapsed());
    await shot(page, "01_pinned");

    // 2. 左侧菜单出现报表
    await page.locator(".ant-drawer-close").click();
    await page.locator(".ant-drawer").waitFor({ state: "detached", timeout: 10000 });
    await page.locator(".ant-menu-item", { hasText: "客户分布" }).first().waitFor({ timeout: 15000 });
    log("菜单出现报表「客户分布」", elapsed());
    await shot(page, "02_menu_added");

    // 3. 打开报表 → 重跑出图表 + 数据 + SQL
    await page.locator(".ant-menu-item", { hasText: "客户分布" }).first().click();
    await page.locator(".ant-layout-content canvas").first().waitFor({ timeout: 15000 });
    const content = page.locator(".ant-layout-content");
    // 展开「数据（N 行）」→ 表格可见
    await content.getByText(/数据（\d+\s*行）/).first().click();
    await page.locator(".ant-layout-content .ant-table").first().waitFor({ timeout: 10000 });
    // 展开「它怎么查的」→ SQL 可见
    await content.getByText("它怎么查的").first().click();
    await page.waitForTimeout(300);
    if (!/customers/i.test(await content.innerText())) {
      failures.push("报表页缺少 SQL 披露（缺 customers）");
    }
    log("报表打开并重跑（图表 + 数据 + SQL）", elapsed());
    await shot(page, "03_dashboard_run");

    // 4. 取消钉住 → 菜单与页面齐清
    await page.getByRole("button", { name: btn("问数据") }).first().click();
    await page.locator(".ant-drawer").getByText("已钉住的报表").first().waitFor({ timeout: 10000 });
    await page.getByRole("button", { name: /取消\s*钉\s*住/ }).first().click();
    await page.getByRole("button", { name: /确\s*定/ }).first().click();
    await page.locator(".ant-drawer").getByText("已钉住的报表").waitFor({ state: "detached", timeout: 10000 });
    await page.locator(".ant-drawer-close").click();
    await page.locator(".ant-drawer").waitFor({ state: "detached", timeout: 10000 });
    await page.waitForTimeout(500);
    if ((await menuCount("客户分布")) > 0) {
      failures.push("取消钉住后菜单「客户分布」仍在");
    }
    log("取消钉住：菜单与页面已清理", elapsed());
    await shot(page, "04_unpinned");
  } catch (e) {
    const dump = await page.evaluate(() => ({
      text: document.body.innerText.slice(0, 600),
      menus: Array.from(document.querySelectorAll(".ant-menu-item")).map((m) => m.innerText),
    })).catch(() => ({}));
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
    console.error("BROWSER_E2E_M43_FAIL");
    for (const f of failures) console.error(" -", f);
    process.exit(1);
  }
  console.log("BROWSER_E2E_M43_OK");
  process.exit(0);
})();
