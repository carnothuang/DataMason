/* M5-9 真浏览器 E2E 验收（Playwright / Chromium）：0.11.0 四轨单会话剧本。
 * 剧本：
 *   A 结构演化：API 增表 visits_m5 → 工作台菜单出现；API 删表 → 菜单/meta 消失
 *   B 附件：给 customers 加 FILE 列 attach_m5 → 表单上传 → 列表出现附件名链接
 *   B' 导入导出：导入向导四步（上传→映射→校验→完成）；rows.xlsx 可取
 *   C 权限：创建操作员（仅 view+own）→ 登录后无「新增」按钮、直调删除 403
 * 验收线：≤5 分钟通过。
 * 前置：python scripts/e2e/run_browser_e2e.py browser_e2e_m5.cjs（serve_for_e2e 预置「客户与订单E2E」+ LLM 桩）。
 */
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const BASE = process.env.E2E_BASE || "http://127.0.0.1:8765";
const LIMIT_MS = 300_000;
const STAMP = String(Date.now() % 1_000_000);
const SHOT_DIR = process.env.E2E_SHOT_DIR || ".workbuddy/tmp";
const HEADLESS = process.env.E2E_HEADED !== "1";
const E2E_USER = process.env.DATAMASON_E2E_USER || "laomao";
const E2E_PASS = process.env.DATAMASON_E2E_PASS || "pw";

const log = (...a) => console.log("[e2e]", ...a);
const table = `visits_m5_${STAMP}`;
const opUser = `opm5${STAMP}`;

async function shot(page, name) {
  const p = `${SHOT_DIR}/e2e_m5_${name}.png`;
  await page.screenshot({ path: p, fullPage: true }).catch(() => {});
  log("screenshot:", p);
}

async function api(page, p, options = {}) {
  return page.evaluate(
    async ({ baseUrl, p, o }) => {
      const headers = {};
      if (o.body) headers["Content-Type"] = "application/json";
      if (o.key) headers["X-API-Key"] = o.key;
      const r = await fetch(baseUrl + "/api/v1" + p, {
        method: o.method || "GET",
        headers,
        body: o.body ? JSON.stringify(o.body) : undefined,
        signal: AbortSignal.timeout(30000),
      });
      let data = null;
      try { data = await r.json(); } catch { /* ignore */ }
      return { status: r.status, data };
    },
    { baseUrl: BASE, p, o: options },
  );
}

async function login(page, user = E2E_USER, pass = E2E_PASS) {
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
  page.on("console", (m) => { if (m.type() === "error") log("console.error:", m.text().slice(0, 250)); });

  const failures = [];
  const t0 = Date.now();
  const elapsed = () => ((Date.now() - t0) / 1000).toFixed(1) + "s";
  const check = (cond, msg) => { if (cond) log("PASS:", msg); else { failures.push(msg); log("FAIL:", msg); } };

  try {
    await login(page);
    const deps = await api(page, "/deployments");
    const dep = (deps.data || []).find((d) => (d.biz_name || "").startsWith("客户与订单E2E"));
    if (!dep) throw new Error("未找到预置部署 客户与订单E2E");
    const did = dep.id;
    log("部署 id=", did, elapsed());

    // ---------- A 结构演化：增表 ----------
    const addSpec = {
      name: table, biz_name: "回访M5",
      columns: [
        { name: "id", type: "INTEGER", is_pk: true, autoincrement: true, nullable: false },
        { name: "note", biz_name: "备注", type: "TEXT" },
      ],
    };
    const prop = await api(page, `/deployments/${did}/tables/propose`, { method: "POST", body: { spec: addSpec } });
    check(prop.status === 200, `增表提案 200（实际 ${prop.status}）`);
    const applied = await api(page, `/deployments/${did}/tables/apply`, { method: "POST", body: { spec: addSpec } });
    check(applied.status === 201, `增表执行 201（实际 ${applied.status}）`);

    await page.goto(`${BASE}/app/${did}`, { waitUntil: "domcontentloaded" });
    await page.getByText("回访M5").first().waitFor({ timeout: 20000 }).catch(() => null);
    check((await page.getByText("回访M5").count()) > 0, "工作台菜单出现新表");
    await shot(page, "A_addTable");

    // ---------- B 附件：加 FILE 列并上传 ----------
    const alter = await api(page, `/deployments/${did}/tables/customers/columns`, {
      method: "POST",
      body: { ops: [{ action: "add", name: `attach_m5_${STAMP}`, biz_name: "附件", type: "FILE", nullable: true }] },
    });
    check(alter.status === 200, `为 customers 增加附件列（实际 ${alter.status}）`);

    // 重新进入工作台让 meta 反映新列
    await page.goto(`${BASE}/app/${did}`, { waitUntil: "domcontentloaded" });
    await page.getByText("客户", { exact: false }).first().waitFor({ timeout: 20000 });
    await page.getByText("客户", { exact: false }).first().click().catch(() => null);
    await page.getByRole("button", { name: /新\s*增/ }).first().waitFor({ timeout: 20000 });
    await page.getByRole("button", { name: /新\s*增/ }).first().click();
    const tmpFile = path.join(SHOT_DIR, `m5_upload_${STAMP}.txt`);
    fs.mkdirSync(SHOT_DIR, { recursive: true });
    fs.writeFileSync(tmpFile, "M5 attachment content");
    const fileInput = page.locator('.ant-drawer input[type="file"]').first();
    await fileInput.setInputFiles(tmpFile);
    await page.waitForTimeout(1500);
    const nameInput = page.locator(".ant-drawer").getByLabel("客户名");
    await nameInput.fill(`M5客户${STAMP}`).catch(async () => {
      await page.locator(".ant-drawer input").first().fill(`M5客户${STAMP}`);
    });
    await page.locator(".ant-drawer").getByRole("button", { name: /新\s*增/ }).click();
    await page.waitForTimeout(2000);
    const chip = page.getByText(path.basename(tmpFile)).first();
    check((await chip.count()) > 0, "列表出现附件文件名链接");
    await shot(page, "B_attachment");

    // ---------- B' 导入向导 ----------
    const csv = path.join(SHOT_DIR, `m5_import_${STAMP}.csv`);
    fs.writeFileSync(csv, `客户名,电话\n导入客户${STAMP},13900000000\n`);
    await page.getByRole("button", { name: /导\s*入/ }).first().click();
    await page.getByText("上传文件").first().waitFor({ timeout: 15000 }).catch(() => null);
    const importInput = page.locator('.ant-modal input[type="file"]');
    await importInput.setInputFiles(csv);
    await page.getByText("列映射").first().waitFor({ timeout: 20000 }).catch(() => null);
    check((await page.getByText("列映射").count()) > 0, "导入向导进入列映射");
    await page.getByRole("button", { name: /校验预览/ }).first().click();
    await page.waitForTimeout(800);
    await page.getByRole("button", { name: /确认导入/ }).first().click();
    await page.getByText(/导入完成/).first().waitFor({ timeout: 20000 }).catch(() => null);
    check((await page.getByText(/导入完成/).count()) > 0, "导入完成提示");
    await shot(page, "B_import");

    // XLSX 导出可取
    const xls = await page.evaluate(async (url) => (await fetch(url)).status,
      `${BASE}/api/v1/app/${did}/api/tables/customers/rows.xlsx`);
    check(xls === 200, `rows.xlsx 可取（实际 ${xls}）`);

    // ---------- C 权限：操作员仅 view+own ----------
    const menus = await api(page, `/app/${did}`);
    const customerPage = (menus.data.pages || []).find((p) => p.table === "customers");
    const customerMenu = (menus.data.menus || []).find((m) => m.ref_id === customerPage.id);
    await api(page, "/auth/users", { method: "POST", body: { username: opUser, password: "pw", display_name: "M5操作员" } }).catch(() => null);
    const mkOp = await api(page, `/deployments/${did}/members`, {
      method: "POST",
      body: {
        username: opUser, role: "operator", menu_ids: [customerMenu.id],
        actions: { [String(customerMenu.id)]: { view: true, create: false, update: false, delete: false, export: false, import: false } },
        data_scope: { [String(customerMenu.id)]: "all" },
      },
    });
    check([201, 200].includes(mkOp.status), `创建操作员（实际 ${mkOp.status}）`);

    const ctx2 = await browser.newContext({ viewport: { width: 1440, height: 960 } });
    const op = await ctx2.newPage();
    await login(op, opUser, "pw");
    await op.goto(`${BASE}/app/${did}`, { waitUntil: "domcontentloaded" });
    await op.getByText("客户", { exact: false }).first().waitFor({ timeout: 20000 }).catch(() => null);
    await op.waitForTimeout(1200);
    check((await op.getByRole("button", { name: /新\s*增/ }).count()) === 0, "操作员无「新增」按钮");
    const rowsList = await api(op, `/app/${did}/api/tables/customers/rows`);
    const somePk = rowsList.data && rowsList.data.data && rowsList.data.data[0] && rowsList.data.data[0].id;
    if (somePk !== undefined && somePk !== null) {
      const denied = await api(op, `/app/${did}/api/tables/customers/rows/${somePk}`, { method: "DELETE" });
      check(denied.status === 403, `操作员直调删除 403（实际 ${denied.status}）`);
    } else {
      log("跳过删除 403 断言（列表为空）");
    }
    await shot(op, "C_permission");
    await ctx2.close();

    // ---------- A 结构演化：删表 ----------
    const drop = await api(page, `/deployments/${did}/tables/${table}/drop`, { method: "POST", body: { confirm: table } });
    check(drop.status === 200, `删表执行 200（实际 ${drop.status}）`);
    const metaAfter = await api(page, `/app/${did}/api/meta`);
    check(!(metaAfter.data && metaAfter.data[table]), "meta 不再含已删表");
  } catch (e) {
    failures.push(`异常：${e.message}`);
    log("EXCEPTION", e.stack || e.message);
    await shot(page, "exception").catch(() => {});
  } finally {
    const ms = Date.now() - t0;
    await browser.close();
    if (failures.length === 0 && ms <= LIMIT_MS) {
      console.log(`BROWSER_E2E_M5_OK ${(ms / 1000).toFixed(1)}s`);
      process.exit(0);
    }
    console.log(`BROWSER_E2E_M5_FAIL ${(ms / 1000).toFixed(1)}s`);
    for (const f of failures) console.log("  -", f);
    process.exit(1);
  }
})();
