/* M4-2 真浏览器 E2E（Playwright / Chromium）：问数据视图卡 + 自绘 ECharts。
 * 验收线：工作台顶栏「问数据」→ 抽屉里问一句 → 视图卡 = 一句话结论 + ECharts 图表 + 可展开「它怎么查的」SQL；
 *        「非数值」问题 → 图表规格不可渲染时退化为表格（不出错）；
 *        操作员登录 → 无「问数据」入口（默认不开放）。
 * 前置：scripts/e2e/serve_for_e2e.py 已在 127.0.0.1:8765 运行、托管 web/dist，且已预置
 *       确定性演示部署「客户与订单E2E」（customers/orders）；LLM 走桩（确定性 SQL/图表/结论）。
 * 运行：python scripts/e2e/run_browser_e2e.py browser_e2e_m42.cjs
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
  const p = `${SHOT_DIR}/e2e_m42_${name}.png`;
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
  const name = `问数据客户${STAMP}`;
  const opUser = `opm42${STAMP}`;
  let dep = null;
  let customerId = null;

  try {
    await login(page, E2E_USER, E2E_PASS);
    log("已登录", elapsed());

    dep = await page.evaluate(async (base) => {
      const r = await fetch(base + "/api/v1/deployments");
      const body = await r.json();
      if (!Array.isArray(body)) return { error: `status=${r.status}` };
      const d = body.find((x) => String(x.biz_name).startsWith("客户与订单E2E"));
      return d ? { entry_path: d.entry_path, id: d.id } : { error: "未找到演示部署" };
    }, BASE);
    if (!dep?.entry_path) throw new Error("取部署失败: " + (dep?.error || "未知"));

    const created = await api(page, `/app/${dep.id}/api/tables/customers/rows`, {
      method: "POST",
      body: { name, phone: "13900002222", remark: "备注文本", vip: 1 },
    });
    if (created.status !== 201) throw new Error("造数据失败: " + created.status + " " + JSON.stringify(created.data));
    customerId = created.data?.id;
    log("已造一条客户数据", elapsed());

    // 1. 进工作台（创建人）→ 顶栏应有「问数据」入口
    await page.goto(BASE + dep.entry_path, { waitUntil: "networkidle" });
    const askBtn = page.getByRole("button", { name: btn("问数据") });
    await askBtn.first().waitFor({ timeout: 15000 });
    log("工作台就绪，看到「问数据」入口", elapsed());
    await shot(page, "01_entry_visible");

    // 2. 问一个「分类」问题 → 视图卡出图表 + 结论 + SQL
    await askBtn.first().click();
    await page.locator(".ant-drawer input.ant-input").waitFor({ timeout: 10000 });
    await page.locator(".ant-drawer input.ant-input").fill("按分类统计客户");
    await page.getByRole("button", { name: btn("问一句") }).click();

    await page.locator(".ant-drawer canvas").first().waitFor({ timeout: 20000 });
    const canvasAfterChart = await page.locator(".ant-drawer canvas").count();
    if (canvasAfterChart < 1) failures.push("视图卡未渲染 ECharts 图表（无 canvas）");
    if ((await page.locator(".ant-drawer").getByText("已列出当前客户。").count()) === 0) {
      failures.push("视图卡未显示一句话结论");
    }
    // 展开「它怎么查的」→ SQL 可见
    await page.locator(".ant-drawer").getByText("它怎么查的").last().click();
    await page.waitForTimeout(300);
    const drawerText = await page.locator(".ant-drawer").innerText();
    if (!/customers/i.test(drawerText)) failures.push("展开后未见 SQL（缺 customers）");
    log("视图卡：结论 + 图表 + 可展开 SQL 均就绪", elapsed());
    await shot(page, "02_view_card_chart");

    // 3. 问「非数值」问题 → 图表规格不可渲染，退化为表格（canvas 数量不增加）
    await page.locator(".ant-drawer input.ant-input").fill("列出非数值字段");
    await page.getByRole("button", { name: btn("问一句") }).click();
    await page.waitForTimeout(1200);
    const canvasAfterDegrade = await page.locator(".ant-drawer canvas").count();
    if (canvasAfterDegrade !== canvasAfterChart) {
      failures.push(`非数值规格未降级为表格（canvas ${canvasAfterChart} → ${canvasAfterDegrade}）`);
    }
    if ((await page.locator(".ant-drawer .ant-table").count()) === 0) {
      failures.push("降级后未见数据表格");
    }
    log("非法/不可渲染图表规格 → 退化为表格，无报错", elapsed());
    await shot(page, "03_view_card_fallback");

    // 关闭抽屉
    await page.keyboard.press("Escape");
    await page.waitForTimeout(300);

    // 4. 操作员默认不开放问数据
    const mkUser = await api(page, "/auth/users", {
      method: "POST",
      body: { username: opUser, password: "pw", display_name: "问数据操作员" },
    });
    if (mkUser.status !== 200 && mkUser.status !== 201) {
      throw new Error("创建操作员失败: " + mkUser.status + " " + JSON.stringify(mkUser.data));
    }
    const itf = await api(page, `/deployments/${dep.id}/interfaces`);
    const menuIds = (itf.data?.menus || []).map((m) => m.id);
    const mkMember = await api(page, `/deployments/${dep.id}/members`, {
      method: "POST",
      body: { username: opUser, role: "operator", menu_ids: menuIds },
    });
    if (mkMember.status !== 200 && mkMember.status !== 201) {
      throw new Error("加操作员成员失败: " + mkMember.status + " " + JSON.stringify(mkMember.data));
    }
    const opCtx = await browser.newContext({ viewport: { width: 1440, height: 960 } });
    const opPage = await opCtx.newPage();
    opPage.on("console", (m) => {
      if (m.type() === "error") log("op.console.error:", m.text().slice(0, 200));
    });
    try {
      await login(opPage, opUser, "pw");
      // 已登录后停留在部署清单：点卡片上的「打开工作台」进入（SPA 客户端跳转）
      const openBtn = opPage.getByRole("button", { name: btn("打开工作台") }).first();
      await openBtn.waitFor({ timeout: 15000 });
      await openBtn.click();
      await opPage.getByRole("button", { name: btn("返回控制台") }).first().waitFor({ timeout: 20000 });
      if ((await opPage.getByRole("button", { name: btn("问数据") }).count()) > 0) {
        failures.push("操作员不应看到「问数据」入口");
      }
      log("操作员无「问数据」入口（默认不开放）", elapsed());
      await shot(opPage, "04_operator_no_entry");
    } catch (e) {
      await shot(opPage, "98_operator_error");
      throw new Error("操作员入口校验失败: " + (e && e.message ? e.message : String(e)) +
        " | url=" + opPage.url());
    } finally {
      await opCtx.close();
    }

    // 清理造的数据（尽力而为：失败不影响验收结论）
    if (customerId != null) {
      try {
        await api(page, `/app/${dep.id}/api/tables/customers/rows/${customerId}`, { method: "DELETE" });
      } catch (e) {
        log("清理数据失败（忽略）:", e && e.message ? e.message : String(e));
      }
    }
  } catch (e) {
    const dump = await page.evaluate(() => ({
      text: document.body.innerText.slice(0, 600),
      canvases: document.querySelectorAll(".ant-drawer canvas").length,
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
    console.error("BROWSER_E2E_M42_FAIL");
    for (const f of failures) console.error(" -", f);
    process.exit(1);
  }
  console.log("BROWSER_E2E_M42_OK");
  process.exit(0);
})();
