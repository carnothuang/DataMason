/* M3-5 真浏览器 E2E（Playwright / Chromium）：访客公开页 + 分享链接绑定身份（DR-04）。
 * 验收线：创建人建「留言」部署 → 匿名（无 cookie context）打开 /public/{id}/{page} 提交成功且无菜单
 *        → 库中归属记系统(created_by=0) → 创建人生成分享链接 → 新用户登录后进 /join/{token} 自动绑定
 *        为操作员并进入工作台，只见授权菜单（替换「成员代录」话术）。
 * 前置：scripts/e2e/serve_for_e2e.py 已在 127.0.0.1:8765 运行、托管 web/dist。不依赖真实 LLM（confirm 直传 spec）。
 * 运行：python scripts/e2e/run_browser_e2e.py browser_e2e_m35.cjs
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

const SPEC = {
  biz_notes: "访客留言",
  tables: [
    {
      name: "messages",
      biz_name: "留言",
      columns: [
        { name: "id", biz_name: "主键", type: "INTEGER", is_pk: true, autoincrement: true, nullable: false },
        { name: "visitor", biz_name: "访客", type: "VARCHAR", length: 64, nullable: false },
        { name: "content", biz_name: "内容", type: "TEXT" },
      ],
      indexes: [],
    },
  ],
};

async function shot(page, name) {
  const p = `${SHOT_DIR}/e2e_m35_${name}.png`;
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
  const failures = [];
  const t0 = Date.now();
  const elapsed = () => ((Date.now() - t0) / 1000).toFixed(1) + "s";
  const opUser = `opb${STAMP}`;
  let did = null;

  try {
    // A. 创建人登录 + 建「留言」部署（confirm 直传 spec，免 LLM）
    const ctxA = await browser.newContext({ viewport: { width: 1440, height: 960 } });
    const pageA = await ctxA.newPage();
    await pageA.goto(BASE + "/login", { waitUntil: "domcontentloaded", timeout: 40000 });
    const login = await api(pageA, "/auth/login", { method: "POST", body: { username: E2E_USER, password: E2E_PASS } });
    if (login.status !== 200) throw new Error("创建人登录失败 " + login.status);
    const conf = await api(pageA, "/deployments/confirm", {
      method: "POST",
      body: { spec_json: SPEC, biz_name: `M35留言板${STAMP}`, seed_data: false },
    });
    if (conf.status !== 201) throw new Error("建部署失败 " + conf.status + " " + JSON.stringify(conf.data));
    did = conf.data.deployment_id;
    const itf = (await api(pageA, `/deployments/${did}/interfaces`)).data;
    const pub = itf.pages.find((p) => p.kind === "public_form");
    const menu = itf.menus[0];
    if (!pub) throw new Error("未生成公开表单页");
    log("部署就绪", did, "公开页", pub.id, elapsed());

    // B. 匿名（独立无 cookie context）：打开 /public 提交，且无任何菜单
    const ctxB = await browser.newContext({ viewport: { width: 900, height: 800 } });
    const pageB = await ctxB.newPage();
    await pageB.goto(`${BASE}/public/${did}/${pub.id}`, { waitUntil: "networkidle", timeout: 40000 });
    await pageB.getByLabel("访客").waitFor({ timeout: 15000 });
    if ((await pageB.locator(".ant-menu").count()) > 0) failures.push("公开页不应出现菜单");
    await pageB.getByLabel("访客").fill("匿名访客B");
    await pageB.getByLabel("内容").fill("来自浏览器的留言");
    await shot(pageB, "01_public_form");
    await pageB.getByRole("button", { name: btn("提交") }).click();
    await pageB.getByText("提交成功", { exact: false }).first().waitFor({ timeout: 15000 });
    log("匿名提交成功且菜单不可见", elapsed());
    await shot(pageB, "02_public_submitted");

    // 库中归属记系统(0)
    const rows = (await api(pageA, `/app/${did}/api/tables/messages/rows`)).data.data;
    const row = rows.find((r) => r.visitor === "匿名访客B");
    if (!row) failures.push("创建人看不到访客提交的行");
    else if (row.created_by !== 0) failures.push("访客行归属列应为系统(0)，实际 " + row.created_by);
    else log("访客行归属=系统(0)", elapsed());

    // C. 创建人生成操作员分享链接
    const link = await api(pageA, `/deployments/${did}/share-links`, {
      method: "POST",
      body: { role: "operator", menu_ids: [menu.id] },
    });
    if (link.status !== 201) throw new Error("生成分享链接失败 " + link.status + " " + JSON.stringify(link.data));
    const token = link.data.token;

    // D. 新用户登录后进 /join/{token} → 自动绑定为操作员并进工作台，只见授权菜单
    await api(pageA, "/auth/users", {
      method: "POST",
      body: { username: opUser, password: "pw", display_name: "浏览器操作员" },
    });
    const ctxC = await browser.newContext({ viewport: { width: 1440, height: 960 } });
    const pageC = await ctxC.newPage();
    await pageC.goto(BASE + "/login", { waitUntil: "domcontentloaded", timeout: 40000 });
    const opLogin = await api(pageC, "/auth/login", { method: "POST", body: { username: opUser, password: "pw" } });
    if (opLogin.status !== 200) throw new Error("操作员登录失败 " + opLogin.status);
    await pageC.goto(`${BASE}/join/${token}`, { waitUntil: "domcontentloaded", timeout: 40000 });
    await pageC.waitForURL(new RegExp(`/app/${did}`), { timeout: 20000 });
    await pageC.locator(".ant-menu-item").first().waitFor({ timeout: 20000 });
    const labels = await pageC.locator(".ant-menu-item").allInnerTexts();
    const joined = labels.some((t) => t.includes(menu.label));
    if (!joined) failures.push(`接受链接后未见授权菜单「${menu.label}」，实际 ${JSON.stringify(labels)}`);
    else log("分享链接绑定为操作员并见授权菜单", elapsed());
    await shot(pageC, "03_joined_workspace");
  } catch (e) {
    const dump = await (async () => {
      try {
        return await pageA.evaluate(() => document.body.innerText.slice(0, 500));
      } catch {
        return "";
      }
    })();
    failures.push("异常: " + (e && e.message ? e.message : String(e)));
    if (dump) failures.push("A页状态: " + dump);
  } finally {
    await browser.close();
  }

  const total = Date.now() - t0;
  log("总耗时", (total / 1000).toFixed(1) + "s", `限额 ${LIMIT_MS / 1000}s`);
  if (total > LIMIT_MS) failures.push(`总耗时 ${total}ms 超过限额`);
  if (failures.length) {
    console.error("BROWSER_E2E_M35_FAIL");
    for (const f of failures) console.error(" -", f);
    process.exit(1);
  }
  console.log("BROWSER_E2E_M35_OK");
  process.exit(0);
})();