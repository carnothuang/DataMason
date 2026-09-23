/* M7 真浏览器 E2E 验收（Playwright / Chromium）：0.13.0 表单控件与界面配置全链路。
 * 剧本（对《开发计划》M7-8 验收逐条）：
 *   ① 工程室打开表单编辑器 → 配置单选 / 多选 / 日期时间 + 双列布局 → 预览 → 保存
 *   ② 工作台新增表单按配置渲染（单选候选项可见）
 *   ③ 绕过前端提交非法单选值 → 服务端 400 拒绝
 *   ④ 多选重新打开完整回填（不多不少）
 *   ⑤ 未配置表按默认映射渲染（零配置零变化）
 * 前置：serve_for_e2e.py 在 127.0.0.1:8765 运行、托管 web/dist；LLM 走确定性桩。
 * 运行：python scripts/e2e/run_browser_e2e.py browser_e2e_m7.cjs
 */
const { chromium } = require("playwright");

const BASE = process.env.E2E_BASE || "http://127.0.0.1:8765";
const LIMIT_MS = 300_000;
const STAMP = String(Date.now() % 1_000_000);
const SHOT_DIR = process.env.E2E_SHOT_DIR || ".workbuddy/tmp";
const HEADLESS = process.env.E2E_HEADED !== "1";
const E2E_USER = process.env.DATAMASON_E2E_USER || "laomao";
const E2E_PASS = process.env.DATAMASON_E2E_PASS || "pw";
const BIZ = `M7表单E2E${STAMP}`;

const log = (...a) => console.log("[e2e]", ...a);
const btn = (label) => new RegExp(label.split("").join("\\s*"));

async function shot(page, name) {
  const p = `${SHOT_DIR}/e2e_m7_${name}.png`;
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

const msg = (page, text) => page.getByText(text, { exact: false });

// M7 专用建库规格：一张工单表，含单选/多选/日期时间/附件候选字段。
const TICKETS_SPEC = {
  biz_notes: "M7 表单控件验收",
  tables: [
    {
      name: "tickets",
      biz_name: "工单",
      columns: [
        { name: "id", biz_name: "主键", type: "INTEGER", is_pk: true, autoincrement: true, nullable: false },
        { name: "title", biz_name: "标题", type: "VARCHAR", length: 64, nullable: false },
        { name: "status", biz_name: "状态", type: "VARCHAR", length: 32 },
        { name: "tags", biz_name: "标签", type: "VARCHAR", length: 255 },
        { name: "due_at", biz_name: "截止时间", type: "DATETIME" },
        { name: "attachment", biz_name: "附件", type: "FILE" },
      ],
      indexes: [],
    },
  ],
};

async function pickControl(page, designer, fieldText, controlLabel) {
  const row = designer.locator("tr.ant-table-row").filter({ hasText: fieldText }).first();
  await row.locator(".ant-select").first().click();
  const opt = page
    .locator(".ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option")
    .filter({ hasText: controlLabel })
    .first();
  await opt.waitFor({ timeout: 8000 });
  await opt.click();
  await page.waitForTimeout(200);
}

async function genOptions(page, designer, fieldText) {
  const row = designer.locator("tr.ant-table-row").filter({ hasText: fieldText }).first();
  await row.getByRole("button", { name: /配置/ }).first().click();
  const modal = page.locator(".ant-modal").filter({ hasText: "候选项：" }).last();
  await modal.waitFor({ timeout: 8000 });
  await modal.getByPlaceholder(/描述候选项来源/).fill(`${fieldText}的常见取值`);
  await modal.getByRole("button", { name: btn("AI 生成") }).click();
  await modal.getByPlaceholder("存储值").first().waitFor({ timeout: 15000 });
  await modal.getByRole("button", { name: btn("完成") }).click();
  await page.waitForTimeout(200);
}

(async () => {
  const browser = await chromium.launch({ headless: HEADLESS, args: ["--no-proxy-server"] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
  page.on("console", (m) => {
    if (m.type() === "error") log("console.error:", m.text().slice(0, 250));
  });
  page.on("response", (r) => {
    if (r.status() >= 400) log(`HTTP ${r.status()} ${r.url()}`);
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

    // 建库（走真实 confirm 管线，绕过 LLM 描述）
    const created = await api(page, "/deployments/confirm", {
      method: "POST",
      body: { spec_json: TICKETS_SPEC, biz_name: BIZ, seed_data: false },
    });
    check(created.status < 300, `建库成功（confirm ${created.status}）`);
    const did = created.data && created.data.deployment_id;
    const entry = (created.data && created.data.entry_path) || `/app/${did}`;

    // ① 打开工作台 → 界面与菜单 → 表单编辑器
    await page.goto(BASE + entry, { waitUntil: "domcontentloaded" });
    await page.locator(".ant-layout-sider, .ant-menu").first().waitFor({ timeout: 20000 }).catch(() => {});
    await page.waitForTimeout(800);
    await page.locator(".ant-menu-item", { hasText: "界面与菜单" }).first().click();
    await msg(page, "界面卡").first().waitFor({ timeout: 15000 });

    const card = page
      .locator("div")
      .filter({ has: page.getByText("数据表：工单", { exact: false }) })
      .last();
    await card.getByRole("button", { name: /调\s*整/ }).first().click();
    const designer = page.locator(".ant-modal").filter({ hasText: "调整表单" }).last();
    await designer.waitFor({ timeout: 10000 });

    // 双列布局
    await designer.getByText("双列", { exact: true }).click();
    // 状态 → 单选 + AI 候选项
    await pickControl(page, designer, "状态", "单选");
    await genOptions(page, designer, "状态");
    // 标签 → 多选 + AI 候选项
    await pickControl(page, designer, "标签", "多选");
    await genOptions(page, designer, "标签");
    // 截止时间 → 日期时间
    await pickControl(page, designer, "截止时间", "日期时间");
    await shot(page, "01_designer");
    await designer.getByRole("button", { name: /保\s*存/ }).first().click();
    await msg(page, "表单配置已保存").first().waitFor({ timeout: 15000 });
    check(true, "① 表单编辑器配置并保存成功");

    // 配置读回校验（API）
    const cfg = await api(page, `/deployments/${did}/forms/tickets`);
    const cf = (cfg.data.fields || []).find((f) => f.field_name === "status");
    check(!!cf && cf.control_type === "radio", "① 配置回读：状态=单选");
    check(cfg.data.layout.columns_per_row === 2, "① 配置回读：每行两列");

    // ② 工作台新增表单按配置渲染
    await page.goto(BASE + entry, { waitUntil: "domcontentloaded" });
    await page.locator(".ant-menu-item", { hasText: "工单" }).first().click();
    await page.getByRole("button", { name: /新\s*增/ }).first().click();
    const drawer = page.locator(".ant-drawer").filter({ hasText: "新增" }).last();
    await drawer.waitFor({ timeout: 10000 });
    const radioCount = await drawer.locator(".ant-radio-wrapper").count();
    check(radioCount >= 3, `② 新增表单渲染单选候选项（${radioCount} 项）`);
    await shot(page, "02_workspace_form");
    await drawer.getByRole("button", { name: /取\s*消/ }).first().click();
    await page.waitForTimeout(300);

    // ③ 绕过前端提交非法单选值 → 服务端拒绝
    const bad = await api(page, `/app/${did}/api/tables/tickets/rows`, {
      method: "POST",
      body: { title: `非法${STAMP}`, status: "unknown" },
    });
    check(bad.status === 400, `③ 非法单选值被服务端拒绝（${bad.status}）`);

    // 合法提交（多选数组）→ 201
    const good = await api(page, `/app/${did}/api/tables/tickets/rows`, {
      method: "POST",
      body: { title: `工单${STAMP}`, status: "new", tags: ["new", "paid"] },
    });
    check(good.status === 201, `④ 合法提交成功（${good.status}）`);
    const rowId = good.data && good.data.id;

    // ④ 多选重新打开完整回填
    const one = await api(page, `/app/${did}/api/tables/tickets/rows/${rowId}`);
    let tags = one.data && one.data.tags;
    try { tags = JSON.parse(tags); } catch { /* ignore */ }
    check(Array.isArray(tags) && tags.length === 2, "④ 多选值完整持久化（2 项）");

    await page.goto(BASE + entry, { waitUntil: "domcontentloaded" });
    await page.locator(".ant-menu-item", { hasText: "工单" }).first().click();
    await page.waitForTimeout(600);
    await page.getByRole("button", { name: /编\s*辑/ }).first().click();
    const editDrawer = page.locator(".ant-drawer").filter({ hasText: "编辑" }).last();
    await editDrawer.waitFor({ timeout: 10000 });
    const checked = await editDrawer.locator(".ant-checkbox-wrapper-checked").count();
    check(checked === 2, `④ 编辑表单回填勾选（${checked}/2）`);
    await shot(page, "03_edit_backfill");
    await editDrawer.getByRole("button", { name: /取\s*消/ }).first().click();

    // ⑤ 未配置表按默认映射（客户与订单E2E 部署的 customers 未配置）
    const list = await api(page, "/deployments");
    const demo = Array.isArray(list.data)
      ? list.data.find((x) => String(x.biz_name).startsWith("客户与订单E2E")) : null;
    if (demo) {
      const rt = await api(page, `/app/${demo.id}/api/forms/customers`);
      const nameField = (rt.data.fields || []).find((f) => f.field_name === "name");
      check(!!nameField && nameField.control_type === "text", "⑤ 零配置字段走默认映射（文本）");
    } else {
      check(false, "⑤ 未找到演示部署用于零配置校验");
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
