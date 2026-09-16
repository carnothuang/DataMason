/* M3-3 真浏览器 E2E（Playwright / Chromium）：演示模式（展厅）。
 * 验收线：进工作台 → 顶栏「演示」一键进入 → 构建痕迹（返回控制台/系统页菜单/运行提示）隐藏、
 *        干净业务菜单+运行界面+数据仍在 → 「脱敏」开关对同一数据呈现两套视图且库中数据零变化
 *        → Ctrl/Cmd+Shift+D 退出，构建痕迹恢复。
 * 前置：scripts/e2e/serve_for_e2e.py 已在 127.0.0.1:8765 运行、托管 web/dist，且已预置
 *       确定性演示部署「客户与订单E2E」（customers/orders，数据为空）。不依赖真实 LLM。
 * 运行：python scripts/e2e/run_browser_e2e.py browser_e2e_m33.cjs
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
  const p = `${SHOT_DIR}/e2e_m33_${name}.png`;
  await page.screenshot({ path: p, fullPage: true }).catch(() => {});
  log("screenshot:", p);
}

// 带登录 cookie 的 page 上直接调用 API（与 SPA 同源）
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
  const browser = await chromium.launch({
    headless: HEADLESS,
    args: ["--no-proxy-server"],
  });
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
  page.on("console", (m) => {
    if (m.type() === "error") log("console.error:", m.text().slice(0, 300));
  });

  const failures = [];
  const t0 = Date.now();
  const elapsed = () => ((Date.now() - t0) / 1000).toFixed(1) + "s";
  const name = `演示客户${STAMP}`;
  const phone = "13900001111";

  const sysPages = ["界面与菜单", "流程编排", "成员与权限", "操作日志"];
  const menuHas = async (text) =>
    (await page.locator(".ant-menu-item", { hasText: text }).count()) > 0;

  try {
    // 0. 登录
    await page.goto(BASE + "/", { waitUntil: "networkidle" });
    if ((await page.getByRole("button", { name: /登\s*录/ }).count()) > 0) {
      await page.getByPlaceholder("用户名").fill(E2E_USER);
      await page.getByPlaceholder("密码").fill(E2E_PASS);
      await page.getByRole("button", { name: /登\s*录/ }).click();
    }
    await page.getByText("我部署过的系统").first().waitFor({ timeout: 15000 });
    log("已登录", elapsed());

    // 1. 取预置部署 + 造一条真实数据（用于观察脱敏两套视图）
    const dep = await page.evaluate(async (base) => {
      const r = await fetch(base + "/api/v1/deployments");
      const body = await r.json();
      if (!Array.isArray(body)) return { error: `status=${r.status}` };
      const d = body.find((x) => String(x.biz_name).startsWith("客户与订单E2E"));
      return d ? { entry_path: d.entry_path, id: d.id, role: d.role } : { error: "未找到演示部署" };
    }, BASE);
    if (!dep.entry_path) throw new Error("取部署失败: " + (dep.error || "未知"));

    const created = await api(page, `/app/${dep.id}/api/tables/customers/rows`, {
      method: "POST",
      body: { name, phone, remark: "演示备注" },
    });
    if (created.status !== 201) throw new Error("造数据失败: " + created.status + " " + JSON.stringify(created.data));
    log("已造一条客户数据", elapsed());

    // 2. 进工作台（首个叶子菜单=客户列表）
    await page.goto(BASE + dep.entry_path, { waitUntil: "networkidle" });
    await page.getByRole("button", { name: btn("新增") }).first().waitFor({ timeout: 15000 });
    await page.locator(".ant-table-tbody").getByText(name, { exact: false }).first().waitFor({ timeout: 15000 });
    log("工作台客户列表就绪（真实数据可见）", elapsed());
    await shot(page, "01_workspace_normal");

    // 进入演示前：构建痕迹应可见（返回控制台 + 创建人的系统页菜单）
    if ((await page.getByRole("button", { name: btn("返回控制台") }).count()) === 0) {
      failures.push("进入演示前未看到「返回控制台」");
    }
    if (!(await menuHas("界面与菜单"))) failures.push("进入演示前未看到系统页菜单「界面与菜单」");

    // 3. 顶栏「演示」一键进入
    await page.getByRole("button", { name: btn("演示") }).click();
    await page.getByText("退出演示", { exact: false }).first().waitFor({ timeout: 10000 });
    log("已进入演示模式", elapsed());
    await shot(page, "02_demo_entered");

    // 3a. 构建痕迹隐藏：返回控制台 / 系统页菜单
    if ((await page.getByRole("button", { name: btn("返回控制台") }).count()) > 0) {
      failures.push("演示模式仍显示「返回控制台」");
    }
    for (const label of sysPages) {
      if (await menuHas(label)) failures.push(`演示模式仍显示系统页菜单「${label}」`);
    }
    // 3b. 只剩干净业务菜单 + 运行界面 + 数据
    if (!(await menuHas("客户"))) failures.push("演示模式业务菜单「客户」丢失");
    if ((await page.locator(".ant-table-tbody").getByText(name, { exact: false }).count()) === 0) {
      failures.push("演示模式运行界面/数据丢失");
    }
    log("构建痕迹已隐藏，业务菜单+界面+数据仍在", elapsed());

    // 4. 脱敏开关：展示层替换为脱敏视图，库中数据零变化
    await page.locator(".ant-switch").click();
    await page.waitForTimeout(400);
    const maskedText = await page.locator(".ant-table-tbody").innerText();
    if (maskedText.includes(name)) failures.push("脱敏开启后列表仍出现真实值");
    if (!maskedText.includes("\u25CF")) failures.push("脱敏开启后未出现掩码字符");
    await shot(page, "03_demo_masked");

    const afterMask = await api(page, `/app/${dep.id}/api/tables/customers/rows?page_size=50`);
    const dbRow = (afterMask.data?.data || []).find((r) => String(r.name) === name);
    if (!dbRow || dbRow.phone !== phone) {
      failures.push("脱敏开启后库中数据发生变化（应零变化）: " + JSON.stringify(dbRow));
    } else {
      log("脱敏开启：展示层掩码、库中数据零变化", elapsed());
    }

    // 5. 关脱敏 → 恢复原始视图（同一数据两套视图）
    await page.locator(".ant-switch").click();
    await page.waitForTimeout(400);
    await page.locator(".ant-table-tbody").getByText(name, { exact: false }).first().waitFor({ timeout: 10000 });
    log("脱敏关闭：恢复原始视图", elapsed());
    await shot(page, "04_demo_unmasked");

    // 6. Ctrl/Cmd+Shift+D 退出演示 → 构建痕迹恢复
    await page.keyboard.press("Control+Shift+D");
    await page.getByRole("button", { name: btn("返回控制台") }).waitFor({ timeout: 10000 });
    if (!(await menuHas("界面与菜单"))) failures.push("退出演示后系统页菜单未恢复");
    if ((await page.getByText("退出演示", { exact: false }).count()) > 0) {
      failures.push("退出演示后顶部提示仍在");
    }
    log("Ctrl/Cmd+Shift+D 退出演示，构建痕迹恢复", elapsed());
    await shot(page, "05_demo_exited");

    // 清理造的数据，保持演示部署可重跑
    await api(page, `/app/${dep.id}/api/tables/customers/rows/${dbRow?.id ?? ""}`, { method: "DELETE" });
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
    console.error("BROWSER_E2E_M33_FAIL");
    for (const f of failures) console.error(" -", f);
    process.exit(1);
  }
  console.log("BROWSER_E2E_M33_OK");
  process.exit(0);
})();