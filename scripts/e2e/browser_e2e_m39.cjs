/* M3-9 M3 全量真浏览器 E2E 验收（Playwright / Chromium，单次 ≤5 分钟）。
 * 一条部署串起 M3 全能力：
 *   ① 停止 → 清单折叠 → 再启动（M3-1）
 *   ② 界面卡拖拽组装菜单 → 操作员视图随之变化（M3-2）
 *   ③ 流程向导三步推进 → 状态字段随步推进（M3-4）
 *   ④ 演示模式进出 + 脱敏开关（M3-3）
 *   ⑤ API key 发放 → 试调用 → 吊销 401（M3-8）
 *   ⑥ 匿名访客提交公开留言页（M3-5）
 *   ⑦ 分享链接绑定成员（M3-5）
 *   ⑧ 导出含全量日志（M3-6）
 *   ⑨ 销毁告别态（M3-7）
 * 前置：serve_for_e2e 已在 127.0.0.1:8765 运行、托管 web/dist。不依赖真实 LLM（confirm 直传 spec）。
 * 运行：python scripts/e2e/run_browser_e2e.py browser_e2e_m39.cjs
 * 环境变量：DATAMASON_E2E_USER / DATAMASON_E2E_PASS（默认 laomao / pw）
 */
const fs = require("fs");
const { chromium } = require("playwright");

const BASE = process.env.E2E_BASE || "http://127.0.0.1:8765";
const LIMIT_MS = 300_000; // 5 分钟
const STAMP = String(Date.now() % 1_000_000);
const SHOT_DIR = process.env.E2E_SHOT_DIR || ".workbuddy/tmp";
const HEADLESS = process.env.E2E_HEADED !== "1";
const E2E_USER = process.env.DATAMASON_E2E_USER || "laomao";
const E2E_PASS = process.env.DATAMASON_E2E_PASS || "pw";
const BIZ = `M39验收系统${STAMP}`;
const OP_USER = `opm39_${STAMP}`;
const SHARE_USER = `sharem39_${STAMP}`;
const VISITOR_VALUE = `匿名访客${STAMP}`;

const log = (...a) => console.log("[e2e]", ...a);
const btn = (label) => new RegExp(label.split("").join("\\s*"));
const failures = [];
const t0 = Date.now();
const elapsed = () => ((Date.now() - t0) / 1000).toFixed(1) + "s";

const SPEC = {
  biz_notes: "M3 全量验收：留言（含状态列）",
  tables: [
    {
      name: "items",
      biz_name: "留言",
      columns: [
        { name: "id", biz_name: "主键", type: "INTEGER", is_pk: true, autoincrement: true, nullable: false },
        { name: "visitor", biz_name: "访客", type: "VARCHAR", length: 64, nullable: false },
        { name: "content", biz_name: "内容", type: "TEXT" },
        { name: "status", biz_name: "进度", type: "VARCHAR", length: 16 },
      ],
      indexes: [],
    },
  ],
};

async function shot(page, name) {
  const p = `${SHOT_DIR}/e2e_m39_${name}.png`;
  await page.screenshot({ path: p, fullPage: true }).catch(() => {});
  log("screenshot:", p);
}

async function api(page, path, options = {}) {
  return page.evaluate(
    async ({ baseUrl, p, o }) => {
      const r = await fetch(baseUrl + "/api/v1" + p, {
        method: o.method || "GET",
        headers: o.body || o.headers ? { "Content-Type": "application/json", ...(o.headers || {}) } : undefined,
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

async function newLoggedInContext(browser, user, pass) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 960 }, acceptDownloads: true });
  const page = await ctx.newPage();
  await page.goto(BASE + "/login", { waitUntil: "domcontentloaded", timeout: 40000 });
  const r = await api(page, "/auth/login", { method: "POST", body: { username: user, password: pass } });
  if (r.status !== 200) throw new Error(`登录失败 ${user}: ${r.status}`);
  return { ctx, page };
}

(async () => {
  const browser = await chromium.launch({ headless: HEADLESS, args: ["--no-proxy-server"] });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 960 }, acceptDownloads: true });
  const page = await ctx.newPage();
  page.on("pageerror", (e) => log("pageerror:", String(e).slice(0, 200)));
  let did = null;
  let apiKey = null;

  try {
    // ===== 0. 创建人登录 + 建「留言」部署 =====
    await page.goto(BASE + "/login", { waitUntil: "domcontentloaded", timeout: 40000 });
    const login = await api(page, "/auth/login", { method: "POST", body: { username: E2E_USER, password: E2E_PASS } });
    if (login.status !== 200) throw new Error("创建人登录失败 " + login.status);
    const conf = await api(page, "/deployments/confirm", {
      method: "POST", body: { spec_json: SPEC, biz_name: BIZ, seed_data: false },
    });
    if (conf.status !== 201) throw new Error("建部署失败 " + conf.status + " " + JSON.stringify(conf.data));
    did = conf.data.deployment_id;
    const entry = conf.data.entry_path;
    let itf = (await api(page, `/deployments/${did}/interfaces`)).data;
    const crudPage = itf.pages.find((p) => p.kind === "crud_list");
    const publicPage = itf.pages.find((p) => p.kind === "public_form");
    log(`部署就绪 id=${did}（${entry}）`, elapsed());

    // ===== ① 停止 → 折叠 → 再启动（M3-1）=====
    await page.goto(BASE + "/deployments", { waitUntil: "networkidle" });
    let card = page.locator(".ant-card", { hasText: BIZ }).first();
    await card.getByRole("button", { name: btn("停止") }).click();
    await page.locator(".ant-modal-confirm").getByRole("button", { name: btn("停止") }).click();
    await page.locator(".ant-message").getByText("已停止").first().waitFor({ timeout: 15000 });
    const group = page.getByText(new RegExp(`已停止（\\d+）`)).first();
    await group.waitFor({ timeout: 15000 });
    log("① 停止后进入「已停止」折叠组", elapsed());
    await group.click(); // 展开
    card = page.locator(".ant-card", { hasText: BIZ }).first();
    await card.getByRole("button", { name: btn("再启动") }).click();
    await page.locator(".ant-message").getByText("已再启动").first().waitFor({ timeout: 15000 });
    await page.locator(".ant-card", { hasText: BIZ }).first()
      .getByRole("button", { name: btn("停止") }).waitFor({ timeout: 15000 });
    log("① 再启动恢复运行", elapsed());

    // ===== ② 拖拽组装菜单 → 操作员视图随之变化（M3-2）=====
    await page.goto(BASE + entry, { waitUntil: "networkidle" });
    await page.locator(".ant-menu-item", { hasText: "界面与菜单" }).click();
    await page.getByText("工作台导航树").waitFor({ timeout: 15000 });
    const dragCard = page.locator("div[draggable]", { hasText: "我要留言" }).first();
    const dropRow = page
      .locator("div[draggable]", { hasText: "留言管理" })
      .filter({ has: page.locator('[data-icon="appstore"]') })
      .first();
    await dragCard.waitFor({ timeout: 10000 });
    // 落在目标行底部 → 同级（顶层）菜单，操作员登录即可直接看到
    await dragCard.dragTo(dropRow, { targetPosition: { x: 20, y: 26 } });
    await page.locator(".ant-message").getByText(/已添加/).first().waitFor({ timeout: 15000 });
    itf = (await api(page, `/deployments/${did}/interfaces`)).data;
    const addedMenu = itf.menus.find((m) => m.ref_id === publicPage.id);
    if (!addedMenu) failures.push("② 拖拽后未生成「我要留言」菜单");
    else log(`② 拖拽组装菜单 OK（menu id=${addedMenu.id} parent=${addedMenu.parent_id}）`, elapsed());
    await shot(page, "02_menu_drag");

    // 挂流程菜单（kind=flow）+ 授权操作员「我要留言」→ 操作员视图随之变化
    const flowResp = await api(page, `/deployments/${did}/flows`, {
      method: "POST",
      body: { name: "入库流程", steps: [
        { name: "收货登记", page_id: crudPage.id, status_field: "status", status_value: "待质检" },
        { name: "质检", page_id: crudPage.id, status_field: "status", status_value: "已质检" },
        { name: "入库确认", page_id: crudPage.id, status_field: "status", status_value: "已入库" },
      ] },
    });
    if (flowResp.status !== 201) throw new Error("建流程失败 " + JSON.stringify(flowResp.data));
    const flowPageId = flowResp.data.page_id;
    const menus = (await api(page, `/deployments/${did}/interfaces`)).data.menus;
    const nodes = menus.map((m) => ({ id: m.id, label: m.label, kind: m.kind, ref_id: m.ref_id, parent_id: m.parent_id }));
    nodes.push({ label: "入库流程", kind: "flow", ref_id: flowPageId, parent_id: null });
    const savedMenus = await api(page, `/deployments/${did}/menus`, { method: "PUT", body: { nodes } });
    if (savedMenus.status !== 200) throw new Error("挂流程菜单失败 " + JSON.stringify(savedMenus.data));
    const flowMenu = savedMenus.data.nodes.find((n) => n.ref_id === flowPageId);

    await api(page, "/auth/users", {
      method: "POST", body: { username: OP_USER, display_name: "操作员小张", password: "pw" },
    });
    const opGrant = await api(page, `/deployments/${did}/members`, {
      method: "POST", body: { username: OP_USER, role: "operator", menu_ids: [addedMenu.id] },
    });
    if (opGrant.status !== 201) throw new Error("加操作员失败 " + JSON.stringify(opGrant.data));
    const op = await newLoggedInContext(browser, OP_USER, "pw");
    try {
      await op.page.goto(BASE + entry, { waitUntil: "networkidle" });
      await op.page.locator(".ant-menu-item", { hasText: "我要留言" }).waitFor({ timeout: 15000 });
      if ((await op.page.locator(".ant-menu-item", { hasText: "入库流程" }).count()) > 0) {
        failures.push("② 操作员不应看到未授权的「入库流程」");
      }
      log("② 操作员视图随授权变化（仅见「我要留言」）", elapsed());
    } finally {
      await op.ctx.close();
    }

    // ===== ③ 流程向导三步推进（M3-4）=====
    await api(page, `/app/${did}/api/tables/items/rows`, {
      method: "POST", body: { visitor: `流程物料${STAMP}` },
    });
    await page.goto(BASE + entry, { waitUntil: "networkidle" });
    await page.locator(".ant-menu-item", { hasText: "入库流程" }).click();
    await page.getByText("当前步骤：", { exact: false }).first().waitFor({ timeout: 15000 });
    for (let i = 0; i < 3; i++) {
      await page.getByRole("button", { name: btn("下一步") }).click();
      await page.locator(".ant-message").getByText(/完成/).first().waitFor({ timeout: 15000 });
    }
    await page.getByText("流程已完成", { exact: false }).first().waitFor({ timeout: 15000 });
    const rows = (await api(page, `/app/${did}/api/tables/items/rows?page_size=50`)).data.data;
    const progressed = rows.find((r) => String(r.visitor).startsWith("流程物料"));
    if (!progressed || progressed.status !== "已入库") {
      failures.push("③ 流程状态未推进到「已入库」: " + JSON.stringify(progressed));
    } else log("③ 流程向导三步推进 → 状态=已入库", elapsed());
    await shot(page, "03_flow_done");

    // ===== ④ 演示模式进出 + 脱敏（M3-3）=====
    await api(page, `/app/${did}/api/tables/items/rows`, {
      method: "POST", body: { visitor: `脱敏观察${STAMP}` },
    });
    await page.goto(BASE + entry, { waitUntil: "networkidle" });
    await page.locator(".ant-menu-item", { hasText: "留言管理" }).click();
    await page.locator(".ant-table-tbody").getByText(`脱敏观察${STAMP}`, { exact: false }).first().waitFor({ timeout: 15000 });
    await page.getByRole("button", { name: btn("演示") }).click();
    await page.getByText("退出演示", { exact: false }).first().waitFor({ timeout: 10000 });
    if ((await page.getByRole("button", { name: btn("返回控制台") }).count()) > 0) {
      failures.push("④ 演示模式仍显示「返回控制台」");
    }
    if ((await page.locator(".ant-menu-item", { hasText: "界面与菜单" }).count()) > 0) {
      failures.push("④ 演示模式仍显示系统页菜单");
    }
    await page.locator(".ant-switch").click();
    await page.waitForTimeout(400);
    const maskedText = await page.locator(".ant-table-tbody").innerText();
    if (maskedText.includes(`脱敏观察${STAMP}`) || !maskedText.includes("\u25CF")) {
      failures.push("④ 脱敏开关未生效");
    }
    await page.locator(".ant-switch").click();
    await page.waitForTimeout(300);
    await page.keyboard.press("Control+Shift+D");
    await page.getByRole("button", { name: btn("返回控制台") }).waitFor({ timeout: 10000 });
    log("④ 演示模式进出 + 脱敏 OK", elapsed());
    await shot(page, "04_demo");

    // ===== ⑤ API key 发放 → 试调用 → 吊销 401（M3-8）=====
    await page.locator(".ant-menu-item", { hasText: "API 文档" }).click();
    await page.getByText("端点清单").waitFor({ timeout: 15000 });
    await page.getByRole("button", { name: btn("发放密钥") }).click();
    const keyModal = page.locator(".ant-modal", { hasText: "发放 API 密钥" }).first();
    await keyModal.locator('input[placeholder="密钥名称"]').fill("M39同步");
    await keyModal.getByRole("button", { name: btn("生成") }).click();
    const fresh = page.locator(".ant-alert", { hasText: "密钥已生成" }).first();
    await fresh.waitFor({ timeout: 15000 });
    apiKey = (await fresh.innerText()).match(/dm_[A-Za-z0-9_\-]+/)?.[0] || null;
    if (!apiKey) failures.push("⑤ 未拿到密钥明文");
    await page.locator(".ant-select", { hasText: "选择数据表" }).first().click();
    await pickOption(page, "items");
    await page.getByRole("button", { name: btn("试调用") }).click();
    await page.locator("pre").filter({ hasText: '"total"' }).first().waitFor({ timeout: 15000 });
    // key 通道试调用
    await page.locator(".ant-select", { hasText: "用当前登录态" }).first().click();
    await pickOption(page, "用 API 密钥");
    await page.getByRole("button", { name: btn("试调用") }).click();
    await page.locator("pre").filter({ hasText: "HTTP 200" }).first().waitFor({ timeout: 15000 });
    log("⑤ API 文档试调用（会话+密钥）返回真实数据", elapsed());

    // 吊销 → 立即 401（页面内 fetch 带 key）
    await page.getByRole("button", { name: btn("吊销") }).first().click();
    await page.locator(".ant-popconfirm").getByRole("button", { name: btn("吊销") }).click();
    await page.locator(".ant-message").getByText(/已吊销/).first().waitFor({ timeout: 15000 });
    const afterRevoke = await page.evaluate(async ({ base, d, k }) => {
      const r = await fetch(`${base}/api/v1/app/${d}/api/tables/items/rows`, { headers: { "X-API-Key": k } });
      return r.status;
    }, { base: BASE, d: did, k: apiKey });
    if (afterRevoke !== 401) failures.push(`⑤ 吊销后应 401，实际 ${afterRevoke}`);
    else log("⑤ 吊销后立即 401", elapsed());
    await shot(page, "05_apikey");

    // ===== ⑥ 匿名访客提交公开留言页（M3-5）=====
    const anonCtx = await browser.newContext({ viewport: { width: 900, height: 800 } });
    const anon = await anonCtx.newPage();
    try {
      await anon.goto(`${BASE}/public/${did}/${publicPage.id}`, { waitUntil: "networkidle", timeout: 40000 });
      await anon.getByLabel("访客").waitFor({ timeout: 15000 });
      if ((await anon.locator(".ant-menu").count()) > 0) failures.push("⑥ 公开页不应有菜单");
      await anon.getByLabel("访客").fill(VISITOR_VALUE);
      await anon.getByLabel("内容").fill("来自访客的留言");
      await anon.getByRole("button", { name: btn("提交") }).click();
      await anon.getByText("提交成功", { exact: false }).first().waitFor({ timeout: 15000 });
      const anonRows = (await api(page, `/app/${did}/api/tables/items/rows?page_size=50`)).data.data;
      const vrow = anonRows.find((r) => r.visitor === VISITOR_VALUE);
      if (!vrow || vrow.created_by !== 0) failures.push("⑥ 访客提交未入库/归属非系统");
      else log("⑥ 匿名访客提交成功（归属系统 0，无菜单）", elapsed());
    } finally {
      await anonCtx.close();
    }

    // ===== ⑦ 分享链接绑定成员（M3-5）=====
    const shareLink = await api(page, `/deployments/${did}/share-links`, {
      method: "POST", body: { role: "operator", menu_ids: [itf.menus[0].id] },
    });
    const token = shareLink.data.token;
    await api(page, "/auth/users", {
      method: "POST", body: { username: SHARE_USER, display_name: "分享加入者", password: "pw" },
    });
    const joiner = await newLoggedInContext(browser, SHARE_USER, "pw");
    try {
      await joiner.page.goto(`${BASE}/join/${token}`, { waitUntil: "domcontentloaded", timeout: 40000 });
      await joiner.page.waitForURL(new RegExp(`/app/${did}`), { timeout: 20000 });
      await joiner.page.locator(".ant-menu-item").first().waitFor({ timeout: 20000 });
      log("⑦ 分享链接绑定为成员并进入工作台", elapsed());
    } finally {
      await joiner.ctx.close();
    }

    // ===== ⑧ 导出含全量日志（M3-6）=====
    await page.goto(BASE + "/deployments", { waitUntil: "networkidle" });
    card = page.locator(".ant-card", { hasText: BIZ }).first();
    const [download] = await Promise.all([
      page.waitForEvent("download", { timeout: 20000 }),
      card.getByRole("button", { name: btn("导出") }).click(),
    ]);
    const dlPath = await download.path();
    const pkg = JSON.parse(fs.readFileSync(dlPath, "utf-8"));
    const acts = new Set((pkg.activity_logs || []).map((lg) => lg.action));
    if (!(pkg.activity_logs && pkg.activity_logs.length > 0)) failures.push("⑧ 导出不含操作日志");
    if (!acts.has("flow")) failures.push("⑧ 导出缺少流程日志");
    if (!(pkg.activity_logs || []).some((lg) => lg.object_table === "items" && lg.action === "create")) {
      failures.push("⑧ 导出缺少业务 create 日志");
    }
    log(`⑧ 导出含全量日志（${pkg.activity_logs.length} 条 committed）`, elapsed());

    // ===== ⑨ 销毁告别态（M3-7）=====
    card = page.locator(".ant-card", { hasText: BIZ }).first();
    await card.getByRole("button", { name: btn("销毁") }).click();
    const dModal = page.locator(".ant-modal", { hasText: "销毁系统" }).first();
    await dModal.getByRole("button", { name: btn("先导出数据") }).waitFor({ timeout: 10000 });
    const destroyBtn = dModal.getByRole("button", { name: btn("销毁") });
    if (!(await destroyBtn.isDisabled())) failures.push("⑨ 未确认时「销毁」应禁用");
    await dModal.getByRole("button", { name: btn("先导出数据") }).click();
    await dModal.getByText("已导出").first().waitFor({ timeout: 15000 });
    await dModal.getByRole("checkbox").check();
    await destroyBtn.click();
    await page.locator(".ant-modal-confirm", { hasText: "最后确认" }).getByRole("button", { name: btn("确认销毁") }).click();
    await page.getByText("已销毁", { exact: false }).first().waitFor({ timeout: 20000 });
    await page.getByRole("button", { name: btn("完成") }).click();
    await page.waitForFunction((t) => !document.body.innerText.includes(t), BIZ, { timeout: 15000 });
    const gone = await api(page, `/app/${did}`);
    if (gone.status !== 404) failures.push(`⑨ 销毁后工作台应 404，实际 ${gone.status}`);
    log("⑨ 销毁告别态 + 清单移除 + 工作台 404", elapsed());
    await shot(page, "09_destroyed");
  } catch (e) {
    const state = await page
      .evaluate(() => ({ url: location.href, text: document.body ? document.body.innerText.slice(0, 500) : "" }))
      .catch(() => ({}));
    failures.push("异常: " + (e && e.message ? e.message : String(e)));
    if (state && state.text) failures.push("页面状态: " + JSON.stringify(state));
    await shot(page, "99_error");
  } finally {
    await browser.close();
  }

  const total = Date.now() - t0;
  log("总耗时", (total / 1000).toFixed(1) + "s", `限额 ${LIMIT_MS / 1000}s`);
  if (total > LIMIT_MS) failures.push(`总耗时 ${total}ms 超过 5 分钟限额`);
  if (failures.length) {
    console.error("BROWSER_E2E_M39_FAIL");
    for (const f of failures) console.error(" -", f);
    process.exit(1);
  }
  console.log("BROWSER_E2E_M39_OK");
  process.exit(0);
})();