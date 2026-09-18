/* M6 真浏览器 E2E 验收（Playwright / Chromium）：0.12.0 混合建库单会话全链路。
 * 剧本（对《开发计划》M6-6 五项验收逐条）：
 *   ① 描述「客户与订单（含回访记录）」→ 骨架图示三表 + 两连线
 *   ② 删一条线 → 图上少一条；再加回 → 图与外键列联动一致
 *   ③ 逐表一句话引导字段 → 图上可见（展开列）；改字段业务名 → 画布即时反映
 *   ④ 留一张表不引导 → 落库被拦 → 补上后部署成功 → 菜单出现可录数据
 *   ⑤ 直连（link）模式无「混合·分步引导」入口
 * 验收线：≤5 分钟通过。前置：serve_for_e2e.py 在 127.0.0.1:8765 运行、托管 web/dist；
 *       LLM 走确定性桩（DATAMASON_E2E_LLM_STUB=1，含 M6 骨架/字段桩）。
 * 运行：python scripts/e2e/run_browser_e2e.py browser_e2e_m6.cjs
 */
const { chromium } = require("playwright");

const BASE = process.env.E2E_BASE || "http://127.0.0.1:8765";
const LIMIT_MS = 300_000;
const STAMP = String(Date.now() % 1_000_000);
const SHOT_DIR = process.env.E2E_SHOT_DIR || ".workbuddy/tmp";
const HEADLESS = process.env.E2E_HEADED !== "1";
const E2E_USER = process.env.DATAMASON_E2E_USER || "laomao";
const E2E_PASS = process.env.DATAMASON_E2E_PASS || "pw";
const BIZ = `M6混合订单E2E${STAMP}`;

const log = (...a) => console.log("[e2e]", ...a);
const btn = (label) => new RegExp(label.split("").join("\\s*"));

async function shot(page, name) {
  const p = `${SHOT_DIR}/e2e_m6_${name}.png`;
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

/** 确认框（App.useApp 的 modal.confirm）点确认按钮。 */
async function confirmModal(page, okText, timeout = 10000) {
  const modal = page.locator(".ant-modal-confirm").last();
  await modal.waitFor({ timeout });
  await modal.getByRole("button", { name: btn(okText) }).click();
}

const msg = (page, text) => page.getByText(text, { exact: false });

(async () => {
  const browser = await chromium.launch({ headless: HEADLESS, args: ["--no-proxy-server"] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
  page.on("console", (m) => {
    if (m.type() === "error") log("console.error:", m.text().slice(0, 250));
  });
  page.on("response", async (r) => {
    if (r.status() < 400 || !/skeleton|confirm/.test(r.url())) return;
    let body = "";
    try { body = (await r.text()).slice(0, 500); } catch { /* ignore */ }
    log(`HTTP ${r.status()} ${r.url()} :: ${body}`);
  });

  const failures = [];
  const t0 = Date.now();
  const elapsed = () => ((Date.now() - t0) / 1000).toFixed(1) + "s";
  const check = (ok, label) => {
    if (!ok) failures.push(label);
    log(ok ? "PASS" : "FAIL", label);
  };

  try {
    await login(page, E2E_USER, E2E_PASS);
    log("创建人已登录", elapsed());

    // 进入部署页 → 打开入口下拉（全自动 / 混合引导 两个入口）
    await page.goto(BASE + "/deployments", { waitUntil: "domcontentloaded" });
    await page.getByText("我部署过的系统").first().waitFor({ timeout: 30000 });
    await page.getByRole("button", { name: btn("部署一套系统") }).click();
    await page.locator(".ant-dropdown-menu-item").first().waitFor({ timeout: 10000 });
    const menuAll = await page.locator(".ant-dropdown-menu-item").allInnerTexts();
    check(menuAll.some((t) => t.includes("全自动") && t.includes("简单业务")),
      "入口下拉给出「全自动（简单业务）」");
    check(menuAll.some((t) => t.includes("混合引导") && t.includes("复杂需求")),
      "入口下拉给出「混合引导（复杂需求）」");

    // ⑤ 全自动向导内不再出现混合入口（混合只走专属入口，DR-39；直连整体无混合）
    await page.getByRole("menuitem", { name: btn("全自动（简单业务）") }).click();
    await page.locator(".ant-modal").filter({ hasText: "部署一套系统" }).first()
      .waitFor({ timeout: 10000 });
    await page.getByText("接已有的库", { exact: false }).click();
    await page.waitForTimeout(300);
    const hybridVisibleInLink =
      (await page.getByText("混合·分步引导", { exact: false }).count()) > 0;
    check(!hybridVisibleInLink, "⑤ 直连模式不显示「混合·分步引导」入口");
    await page.locator(".ant-modal").filter({ hasText: "部署一套系统" })
      .getByRole("button", { name: btn("取消") }).click();
    await page.waitForTimeout(300);

    // 选「混合引导（复杂需求）」→ 直达分步引导弹窗
    await page.getByRole("button", { name: btn("部署一套系统") }).click();
    await page.getByRole("menuitem", { name: btn("混合引导（复杂需求）") }).click();
    const hybrid = page.locator(".ant-modal").filter({ hasText: "混合建库（分步引导）" });
    await hybrid.waitFor({ timeout: 10000 });

    // Step 0：意图
    await hybrid.getByPlaceholder("如：客户与订单管理，含客户回访记录")
      .fill("客户与订单（含回访记录）");
    await hybrid.getByPlaceholder("给这套系统起个名").fill(BIZ);
    await hybrid.getByText("就绪即带演示数据", { exact: false }).click(); // 取消勾选，避免种子
    await hybrid.getByRole("button", { name: btn("生成表骨架") }).click();

    // Step 1：骨架图（三表 + 两连线）
    const hsvg = hybrid.locator("svg:has(marker)").first();
    await hsvg.waitFor({ timeout: 20000 });
    const hnode = (text) => hsvg.getByText(text, { exact: true }).first();
    const hedgeCount = () =>
      hsvg.locator("text", { hasText: "customers_id · 点击删除" }).count();
    for (const t of ["客户", "订单", "回访记录"]) {
      check((await hnode(t).count()) > 0, `① 骨架图含表「${t}」`);
    }
    let edges = await hedgeCount();
    check(edges === 2, `① 骨架图含 2 条关系连线（实际 ${edges}）`);
    await shot(page, "01_skeleton");

    // ② 删一条线（回访记录 → 客户 = 第 2 条）
    await hsvg.locator("text", { hasText: "customers_id · 点击删除" }).nth(1).click();
    await confirmModal(page, "删除");
    edges = await hedgeCount();
    check(edges === 1, `② 删线后图剩 1 条（实际 ${edges}）`);

    // ② 加回一条线：回访记录「连线」→ 点客户节点
    await hybrid.getByText("连线", { exact: true }).nth(2).click();
    await hnode("客户").click();
    await confirmModal(page, "建立关系");
    edges = await hedgeCount();
    check(edges === 2, `② 加线后图恢复 2 条（实际 ${edges}）`);

    // Step 2：逐表引导字段（先订单、回访；客户留到最后验证拦截）
    await hybrid.getByRole("button", { name: btn("下一步：逐表字段") }).click();
    await hnode("订单").click();
    await hybrid.getByPlaceholder("如：客户表要记姓名、电话、VIP 等级、入会时间")
      .fill("订单要记金额和说明");
    await hybrid.getByRole("button", { name: btn("生成字段") }).click();
    await msg(page, "已为「订单」生成").first().waitFor({ timeout: 20000 });

    await hnode("回访记录").click();
    await hybrid.getByPlaceholder("如：客户表要记姓名、电话、VIP 等级、入会时间")
      .fill("回访要记内容、回访时间和结果");
    await hybrid.getByRole("button", { name: btn("生成字段") }).click();
    await msg(page, "已为「回访记录」生成").first().waitFor({ timeout: 20000 });
    await shot(page, "02_guided_two");

    // ③ 图上可见：展开回访记录节点，看到引导出的业务字段
    await hsvg.getByText(/^展开 \d+ 列$/).last().click();
    const followupField = await hsvg.getByText("回访内容", { exact: false }).count();
    check(followupField > 0, "③ 引导字段在画布节点上可见（回访内容）");

    // ③ 改字段 → 画布即时反映（单一数据源）：删掉回访记录的业务字段
    await hybrid.getByRole("button", { name: btn("删除") }).nth(1).click();
    const afterRemove = await hsvg.getByText("回访内容", { exact: false }).count();
    check(afterRemove === 0, "③ 删字段后画布即时反映（回访内容消失）");

    // ④ 留客户表不引导 → 全量确认被拦
    await hybrid.getByRole("button", { name: btn("下一步：全量确认") }).click();
    const warn = await hybrid.getByText("还有表没引导字段", { exact: false }).count();
    check(warn > 0, "④ 全量确认页明示未引导表");
    await hybrid.getByRole("button", { name: btn("合并并部署") }).click();
    await msg(page, "还有表没引导字段：customers").first().waitFor({ timeout: 15000 });
    check(true, "④ 落库被拦（提示 customers 未引导）");

    // 补上客户表字段 → 部署成功
    await hnode("客户").click();
    await hybrid.getByPlaceholder("如：客户表要记姓名、电话、VIP 等级、入会时间")
      .fill("客户要记姓名、电话、VIP 等级、入会时间和备注");
    await hybrid.getByRole("button", { name: btn("生成字段") }).click();
    await msg(page, "已为「客户」生成").first().waitFor({ timeout: 20000 });
    await hybrid.getByRole("button", { name: btn("下一步：全量确认") }).click();
    await hybrid.getByText("字段已就绪，可以落库").first().waitFor({ timeout: 10000 });
    check(true, "④ 补全后全量确认通过");
    await hybrid.getByRole("button", { name: btn("合并并部署") }).click();
    await page.getByRole("button", { name: btn("进入工作台") }).first()
      .waitFor({ timeout: 60000 });
    check(true, "④ 混合建库部署成功（到达就绪态）");
    await shot(page, "03_deployed");

    // ④ 菜单出现 + 可录数据
    const list = await api(page, "/deployments");
    const dep = Array.isArray(list.data)
      ? list.data.find((x) => String(x.biz_name) === BIZ) : null;
    check(!!dep, `④ 部署清单出现「${BIZ}」`);
    if (dep) {
      await page.goto(BASE + dep.entry_path, { waitUntil: "domcontentloaded" });
      await page.locator(".ant-layout-sider, .ant-menu").first().waitFor({ timeout: 20000 }).catch(() => {});
      await page.waitForTimeout(800);
      const menuTexts = await page.locator(".ant-menu-item").allInnerTexts();
      log("工作台菜单:", JSON.stringify(menuTexts));
      await shot(page, "04_workspace");
      for (const t of ["客户", "订单", "回访记录"]) {
        const n = await page.locator(".ant-menu-item", { hasText: t }).count();
        check(n > 0, `④ 工作台菜单出现「${t}」`);
      }
      const created = await api(page, `/app/${dep.id}/api/tables/customers/rows`, {
        method: "POST",
        body: { name: `M6客户${STAMP}` },
      });
      check(created.status < 300, `④ 可录入数据（create ${created.status}）`);
      const rows = await api(page, `/app/${dep.id}/api/tables/customers/rows?page_size=50`);
      const hit = JSON.stringify(rows.data || {}).includes(`M6客户${STAMP}`);
      check(hit, "④ 列表可读到新录入的数据");
    }
  } catch (e) {
    failures.push("异常：" + (e && e.message ? e.message : String(e)));
    await shot(page, "99_error");
  } finally {
    const secs = (Date.now() - t0) / 1000;
    log(`耗时 ${secs.toFixed(1)}s；失败 ${failures.length} 项`);
    if (secs > LIMIT_MS / 1000) failures.push(`超时 > ${LIMIT_MS / 1000}s`);
    if (failures.length) {
      console.log("[e2e] FAILURES:\n - " + failures.join("\n - "));
    } else {
      console.log("[e2e] ALL PASS");
    }
    await browser.close();
  }
  process.exit(failures.length ? 1 : 0);
})();
