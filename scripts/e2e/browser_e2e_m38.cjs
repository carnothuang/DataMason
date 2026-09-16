/* M3-8 真浏览器 E2E（Playwright / Chromium）：API 文档页（DR-11）。
 * 验收线：创建人进工作台 → 「API 文档」系统页 → 端点清单/接入信息可见 → 发放密钥（明文仅显示一次）
 *        → 试调用返回真实数据（会话 + API 密钥两条通道）。
 * 前置：scripts/e2e/serve_for_e2e.py 已在 127.0.0.1:8765 运行、托管 web/dist，且已预置「客户与订单E2E」。
 * 运行：python scripts/e2e/run_browser_e2e.py browser_e2e_m38.cjs
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
  const p = `${SHOT_DIR}/e2e_m38_${name}.png`;
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

async function pickOption(page, text) {
  const dropdown = page.locator(".ant-select-dropdown:visible");
  const opt = dropdown.locator(".ant-select-item-option", { hasText: text }).first();
  await opt.waitFor({ timeout: 10000 });
  await opt.click();
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
    const dep = await page.evaluate(async (base) => {
      const body = await (await fetch(base + "/api/v1/deployments")).json();
      const d = body.find((x) => String(x.biz_name).startsWith("客户与订单E2E"));
      return d ? { entry_path: d.entry_path, id: d.id } : null;
    }, BASE);
    if (!dep) throw new Error("未找到 E2E 演示部署");

    await page.goto(BASE + dep.entry_path, { waitUntil: "networkidle" });
    await page.locator(".ant-menu-item", { hasText: "API 文档" }).click();
    await page.getByText("端点清单").waitFor({ timeout: 15000 });
    if ((await page.getByText("X-API-Key", { exact: false }).count()) === 0) {
      failures.push("接入信息缺少 X-API-Key 说明");
    }
    if ((await page.getByText("调用示例").count()) === 0) failures.push("缺少调用示例");
    log("API 文档页就绪（端点清单/接入信息/示例）", elapsed());
    await shot(page, "01_docs");

    // 发放密钥 → 明文仅显示一次
    await page.getByRole("button", { name: btn("发放密钥") }).click();
    const modal = page.locator(".ant-modal", { hasText: "发放 API 密钥" }).first();
    await modal.locator('input[placeholder="密钥名称"]').fill(`浏览器同步${STAMP}`);
    await modal.getByRole("button", { name: btn("生成") }).click();
    const fresh = page.locator(".ant-alert", { hasText: "密钥已生成" }).first();
    await fresh.waitFor({ timeout: 15000 });
    const freshText = await fresh.innerText();
    if (!freshText.includes("dm_")) failures.push("生成的密钥明文未显示");
    log("已发放密钥（明文仅显示一次）", elapsed());
    await shot(page, "02_key_created");

    // 试调用（会话通道）→ 真实数据
    await page.locator(".ant-select", { hasText: "选择数据表" }).first().click();
    await pickOption(page, "customers");
    await page.getByRole("button", { name: btn("试调用") }).click();
    await page.locator("pre").filter({ hasText: '"total"' }).first().waitFor({ timeout: 15000 });
    log("试调用（会话）返回真实数据", elapsed());

    // 试调用（API 密钥通道）
    await page.locator(".ant-select", { hasText: "用当前登录态" }).first().click();
    await pickOption(page, "用 API 密钥");
    await page.getByRole("button", { name: btn("试调用") }).click();
    await page.locator("pre").filter({ hasText: "HTTP 200" }).first().waitFor({ timeout: 15000 });
    const resultText = await page.locator("pre").last().innerText();
    if (!resultText.includes('"total"')) failures.push("key 通道试调用未返回真实数据");
    else log("试调用（API 密钥）返回真实数据", elapsed());
    await shot(page, "03_try_call");
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
    console.error("BROWSER_E2E_M38_FAIL");
    for (const f of failures) console.error(" -", f);
    process.exit(1);
  }
  console.log("BROWSER_E2E_M38_OK");
  process.exit(0);
})();