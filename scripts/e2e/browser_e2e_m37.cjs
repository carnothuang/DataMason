/* M3-7 真浏览器 E2E（Playwright / Chromium）：销毁（创建人独有 DR-14）。
 * 验收线：创建人建部署 → 部署卡「销毁」→ 弹窗出现「先导出数据」提示与「二次确认」勾选 → 勾选前销毁禁用
 *        → 先导出（出现「已导出」）→ 勾选后可销毁 → 最后确认 → 告别态 → 清单不再显示、卡片消失。
 * 前置：scripts/e2e/serve_for_e2e.py 已在 127.0.0.1:8765 运行、托管 web/dist。不依赖真实 LLM（confirm 直传 spec）。
 * 运行：python scripts/e2e/run_browser_e2e.py browser_e2e_m37.cjs
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
const BIZ = `M37销毁浏览器${STAMP}`;

const log = (...a) => console.log("[e2e]", ...a);
const btn = (label) => new RegExp(label.split("").join("\\s*"));

const SPEC = {
  biz_notes: "待销毁",
  tables: [
    {
      name: "notes",
      biz_name: "笔记",
      columns: [
        { name: "id", biz_name: "主键", type: "INTEGER", is_pk: true, autoincrement: true, nullable: false },
        { name: "title", biz_name: "标题", type: "VARCHAR", length: 80 },
      ],
      indexes: [],
    },
  ],
};

async function shot(page, name) {
  const p = `${SHOT_DIR}/e2e_m37_${name}.png`;
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

(async () => {
  const browser = await chromium.launch({ headless: HEADLESS, args: ["--no-proxy-server"] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
  page.on("console", (m) => {
    if (m.type() === "error") log("console.error:", m.text().slice(0, 200));
  });
  const failures = [];
  const t0 = Date.now();
  const elapsed = () => ((Date.now() - t0) / 1000).toFixed(1) + "s";

  try {
    await page.goto(BASE + "/login", { waitUntil: "domcontentloaded", timeout: 40000 });
    const login = await api(page, "/auth/login", { method: "POST", body: { username: E2E_USER, password: E2E_PASS } });
    if (login.status !== 200) throw new Error("登录失败 " + login.status);

    const conf = await api(page, "/deployments/confirm", {
      method: "POST",
      body: { spec_json: SPEC, biz_name: BIZ, seed_data: false },
    });
    if (conf.status !== 201) throw new Error("建部署失败 " + conf.status);
    const did = conf.data.deployment_id;
    log("部署就绪", did, elapsed());

    await page.goto(BASE + "/deployments", { waitUntil: "networkidle" });
    const card = page.locator(".ant-card", { hasText: BIZ }).first();
    await card.getByRole("button", { name: btn("销毁") }).click();

    // 导出提示 + 二次确认（勾选）出现
    const modal = page.locator(".ant-modal", { hasText: "销毁系统" }).first();
    await modal.getByRole("button", { name: btn("先导出数据") }).waitFor({ timeout: 10000 });
    await modal.getByText("第二步：确认销毁").waitFor({ timeout: 10000 });
    log("销毁弹窗：导出提示 + 二次确认 出现", elapsed());
    await shot(page, "01_destroy_modal");

    const destroyBtn = modal.getByRole("button", { name: btn("销毁") });
    if (!(await destroyBtn.isDisabled())) failures.push("未勾选确认时「销毁」应禁用");

    // 先导出
    await modal.getByRole("button", { name: btn("先导出数据") }).click();
    await modal.getByText("已导出").waitFor({ timeout: 15000 });
    log("已先导出数据", elapsed());

    // 勾选确认 → 可销毁
    await modal.getByRole("checkbox").check();
    if (await destroyBtn.isDisabled()) failures.push("勾选确认后「销毁」应可用");

    // 二次确认弹窗
    await destroyBtn.click();
    const confirm = page.locator(".ant-modal-confirm", { hasText: "最后确认" }).first();
    await confirm.waitFor({ timeout: 10000 });
    await confirm.getByRole("button", { name: btn("确认销毁") }).click();

    // 告别态 + 卡片消失
    await page.getByText("已销毁", { exact: false }).first().waitFor({ timeout: 20000 });
    await page.getByRole("button", { name: btn("完成") }).click();
    await page.waitForFunction((t) => !document.body.innerText.includes(t), BIZ, { timeout: 15000 });
    log("销毁完成：告别态出现，清单不再显示", elapsed());
    await shot(page, "02_destroyed");

    // 工作台 404
    const ws = await api(page, `/app/${did}`);
    if (ws.status !== 404) failures.push(`销毁后工作台应 404，实际 ${ws.status}`);
  } catch (e) {
    failures.push("异常: " + (e && e.message ? e.message : String(e)));
    await shot(page, "99_error");
  } finally {
    await browser.close();
  }

  const total = Date.now() - t0;
  log("总耗时", (total / 1000).toFixed(1) + "s", `限额 ${LIMIT_MS / 1000}s`);
  if (total > LIMIT_MS) failures.push(`总耗时 ${total}ms 超过限额`);
  if (failures.length) {
    console.error("BROWSER_E2E_M37_FAIL");
    for (const f of failures) console.error(" -", f);
    process.exit(1);
  }
  console.log("BROWSER_E2E_M37_OK");
  process.exit(0);
})();