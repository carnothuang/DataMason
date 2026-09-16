/* M4-5 真浏览器 E2E 验收（Playwright / Chromium）：M4（问数据/视图卡/钉住报表/生成治理）单会话全链路。
 * 剧本：创建人登录 → 工作台「问数据」→ 视图卡（结论 + ECharts + 可展开 SQL）→ 钉住
 *       → 左侧菜单出现报表 → 打开报表重跑（图表/数据/SQL）→ 设置页「AI 生成」出现问数据记录
 *       → 操作员（未授权该报表）登录：无「问数据」入口、无未授权报表。
 * 验收线：≤5 分钟通过（离线只读门对抗用例见 tests/test_m4_1_qa.py）。
 * 前置：serve_for_e2e.py 在 127.0.0.1:8765 运行、托管 web/dist，预置演示部署「客户与订单E2E」；LLM 走桩。
 * 运行：python scripts/e2e/run_browser_e2e.py browser_e2e_m45.cjs
 */
const { chromium } = require("playwright");

const BASE = process.env.E2E_BASE || "http://127.0.0.1:8765";
const LIMIT_MS = 300_000;
const STAMP = String(Date.now() % 1_000_000);
const SHOT_DIR = process.env.E2E_SHOT_DIR || ".workbuddy/tmp";
const HEADLESS = process.env.E2E_HEADED !== "1";
const E2E_USER = process.env.DATAMASON_E2E_USER || "laomao";
const E2E_PASS = process.env.DATAMASON_E2E_PASS || "pw";

const log = (...a) => console.log("[e2e]", ...a);
const btn = (label) => new RegExp(label.split("").join("\\s*"));

async function shot(page, name) {
  const p = `${SHOT_DIR}/e2e_m45_${name}.png`;
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
  await page.goto(BASE + "/", { waitUntil: "domcontentloaded" });
  const username = page.getByPlaceholder("用户名");
  const heading = page.getByText("我部署过的系统").first();
  await Promise.race([
    username.first().waitFor({ timeout: 30000 }).catch(() => null),
    heading.waitFor({ timeout: 30000 }).catch(() => null),
  ]);
  if ((await username.count()) > 0) {
    await username.first().fill(user);
    await page.getByPlaceholder("密码").fill(pass);
    await page.getByRole("button", { name: /登\s*录/ }).first().click();
  }
  await heading.waitFor({ timeout: 30000 });
}

(async () => {
  const browser = await chromium.launch({ headless: HEADLESS, args: ["--no-proxy-server"] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
  page.on("console", (m) => {
    if (m.type() === "error") log("console.error:", m.text().slice(0, 250));
  });

  const failures = [];
  const t0 = Date.now();
  const elapsed = () => ((Date.now() - t0) / 1000).toFixed(1) + "s";
  const menuCount = (text) => page.locator(".ant-menu-item", { hasText: text }).count();
  const customerName = `M45客户${STAMP}`;
  const opUser = `opm45${STAMP}`;
  let dep = null;
  let customerId = null;

  try {
    await login(page, E2E_USER, E2E_PASS);
    log("创建人已登录", elapsed());

    dep = await page.evaluate(async (base) => {
      const r = await fetch(base + "/api/v1/deployments");
      const body = await r.json();
      const d = Array.isArray(body)
        ? body.find((x) => String(x.biz_name).startsWith("客户与订单E2E")) : null;
      return d ? { entry_path: d.entry_path, id: d.id } : { error: "未找到演示部署" };
    }, BASE);
    if (!dep?.entry_path) throw new Error("取部署失败: " + (dep?.error || "未知"));

    // 清理历史钉住 + 造一条客户（保证图表有数据）
    for (const p of (await api(page, `/deployments/${dep.id}/qa/pins`)).data?.pins || []) {
      await api(page, `/deployments/${dep.id}/qa/pins/${p.page_id}`, { method: "DELETE" });
    }
    const created = await api(page, `/app/${dep.id}/api/tables/customers/rows`, {
      method: "POST", body: { name: customerName, phone: "13900003333", vip: 1 },
    });
    if (created.status !== 201) throw new Error("造数据失败: " + created.status);
    customerId = created.data?.id;

    // 进工作台
    const demoCard = page.locator(".ant-card", { hasText: "客户与订单E2E" }).first();
    const openBtn = demoCard.getByRole("button", { name: btn("打开工作台") }).first();
    await openBtn.waitFor({ timeout: 15000 });
    await openBtn.click();

    // 1. 问一句 → 视图卡（结论 + 图表 + 可展开 SQL）
    await page.getByRole("button", { name: btn("问数据") }).first().click();
    await page.locator(".ant-drawer input.ant-input").waitFor({ timeout: 10000 });
    await page.locator(".ant-drawer input.ant-input").fill("按分类统计客户");
    await page.getByRole("button", { name: btn("问一句") }).click();
    await page.locator(".ant-drawer canvas").first().waitFor({ timeout: 20000 });
    if ((await page.locator(".ant-drawer").getByText("已列出当前客户。").count()) === 0) {
      failures.push("视图卡未显示一句话结论");
    }
    await page.locator(".ant-drawer").getByText("它怎么查的").last().click();
    await page.waitForTimeout(300);
    if (!/customers/i.test(await page.locator(".ant-drawer").innerText())) {
      failures.push("视图卡「它怎么查的」未披露 SQL");
    }
    log("视图卡：结论 + ECharts + 可展开 SQL", elapsed());
    await shot(page, "01_view_card");

    // 2. 钉住 → 菜单出现报表
    await page.getByRole("button", { name: btn("钉住") }).first().click();
    await page.locator(".ant-drawer").getByText("已钉住").first().waitFor({ timeout: 10000 });
    await page.locator(".ant-drawer-close").click();
    await page.locator(".ant-drawer").waitFor({ state: "detached", timeout: 10000 });
    await page.locator(".ant-menu-item", { hasText: "客户分布" }).first().waitFor({ timeout: 15000 });
    log("钉住成功，菜单出现报表「客户分布」", elapsed());
    await shot(page, "02_pinned_menu");

    // 3. 打开报表 → 重跑（图表 + 数据 + SQL）
    await page.locator(".ant-menu-item", { hasText: "客户分布" }).first().click();
    await page.locator(".ant-layout-content canvas").first().waitFor({ timeout: 15000 });
    const content = page.locator(".ant-layout-content");
    await content.getByText(/数据（\d+\s*行）/).first().click();
    await page.locator(".ant-layout-content .ant-table").first().waitFor({ timeout: 10000 });
    await content.getByText("它怎么查的").first().click();
    await page.waitForTimeout(300);
    if (!/customers/i.test(await content.innerText())) failures.push("报表页缺少 SQL 披露");
    log("报表打开并重跑（图表 + 数据 + SQL）", elapsed());
    await shot(page, "03_dashboard_run");

    // 4. 治理页出现问数据记录（平台管理员）
    await page.goto(BASE + "/settings", { waitUntil: "networkidle" });
    await page.getByRole("tab", { name: "AI 生成" }).click();
    await page.locator(".ant-tabs-tabpane-active .ant-table-tbody").waitFor({ state: "attached", timeout: 15000 });
    await page.waitForTimeout(300);
    if (!(await page.locator(".ant-tabs-tabpane-active").innerText()).includes("问数据")) {
      failures.push("治理页未见「问数据」生成记录");
    }
    log("治理页出现问数据记录", elapsed());
    await shot(page, "04_governance");

    // 5. 操作员（未授权该报表）：无问数据入口、无未授权报表
    const mk = await api(page, "/auth/users", {
      method: "POST", body: { username: opUser, password: "pw", display_name: "M45操作员" },
    });
    if (mk.status !== 200 && mk.status !== 201) throw new Error("建操作员失败: " + mk.status);
    const itf = await api(page, `/deployments/${dep.id}/interfaces`);
    const grantMenus = (itf.data?.menus || [])
      .filter((m) => m.label !== "客户分布")   // 不授权钉住的报表
      .map((m) => m.id);
    const mm = await api(page, `/deployments/${dep.id}/members`, {
      method: "POST", body: { username: opUser, role: "operator", menu_ids: grantMenus },
    });
    if (mm.status !== 200 && mm.status !== 201) throw new Error("加成员失败: " + mm.status);

    const opCtx = await browser.newContext({ viewport: { width: 1440, height: 960 } });
    const opPage = await opCtx.newPage();
    try {
      await login(opPage, opUser, "pw");
      const opCard = opPage.locator(".ant-card", { hasText: "客户与订单E2E" }).first();
      const opOpen = opCard.getByRole("button", { name: btn("打开工作台") }).first();
      await opOpen.waitFor({ timeout: 15000 });
      await opOpen.click();
      await opPage.getByRole("button", { name: btn("返回控制台") }).first().waitFor({ timeout: 20000 });
      if ((await opPage.getByRole("button", { name: btn("问数据") }).count()) > 0) {
        failures.push("操作员不应看到「问数据」入口");
      }
      if ((await opPage.locator(".ant-menu-item", { hasText: "客户分布" }).count()) > 0) {
        failures.push("操作员不应看到未授权的报表菜单");
      }
      log("操作员视图裁剪：无问数据入口、无未授权报表", elapsed());
      await shot(opPage, "05_operator_view");
    } catch (e) {
      await shot(opPage, "98_operator_error");
      const opText = await opPage
        .evaluate(() => document.body.innerText.slice(0, 400))
        .catch(() => "(dump failed)");
      throw new Error(
        (e && e.message ? e.message : String(e)) + " | opUrl=" + opPage.url() + " | opText=" + opText,
      );
    } finally {
      await opCtx.close();
    }
  } catch (e) {
    const dump = await page.evaluate(() => ({
      text: document.body.innerText.slice(0, 500),
      menus: Array.from(document.querySelectorAll(".ant-menu-item")).map((m) => m.innerText),
    })).catch(() => ({}));
    failures.push("异常: " + (e && e.message ? e.message : String(e)));
    failures.push("页面状态: " + JSON.stringify(dump));
    await shot(page, "99_error");
  } finally {
    // 收尾（尽力而为）：取消钉住 + 删除测试客户
    if (dep) {
      try {
        for (const p of (await api(page, `/deployments/${dep.id}/qa/pins`)).data?.pins || []) {
          await api(page, `/deployments/${dep.id}/qa/pins/${p.page_id}`, { method: "DELETE" });
        }
        if (customerId != null) {
          await api(page, `/app/${dep.id}/api/tables/customers/rows/${customerId}`, { method: "DELETE" });
        }
      } catch (e) {
        log("收尾失败（忽略）:", e && e.message ? e.message : String(e));
      }
    }
    await browser.close();
  }

  const total = Date.now() - t0;
  log("总耗时", (total / 1000).toFixed(1) + "s", `限额 ${LIMIT_MS / 1000}s`);
  if (total > LIMIT_MS) failures.push(`总耗时 ${total}ms 超过限额`);

  if (failures.length) {
    console.error("BROWSER_E2E_M45_FAIL");
    for (const f of failures) console.error(" -", f);
    process.exit(1);
  }
  console.log("BROWSER_E2E_M45_OK");
  process.exit(0);
})();
