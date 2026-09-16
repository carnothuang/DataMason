/* M2-9 M2 全量 E2E 验收（Playwright / Chromium，单 context 顺序双角色）。
 * 场景：创建人部署 → 加成员/按人授权 → 操作员登录只见授权菜单+我的记录
 *       → 管理员全量（含操作员记录与日志）→ 直连模式建一套出 CRUD。
 * 实现要点：
 * - 创建模式部署走真实向导（描述→提案→确认），LLM 由 serve 进程内桩提供
 *   （DATAMASON_E2E_LLM_STUB=1，确定性结构，免真实模型）；
 * - 直连部署走「接已有的库」向导，真实反射 serve 预置的 E2E外部产品库；
 * - 单浏览器 context + 顺序登录/退出（复用 M2-5 规避多 context 连接池挂起）；
 * - API 以 SPA 同源 fetch 调用（带登录 cookie）。
 * 前置：scripts/e2e/serve_for_e2e.py 已在 127.0.0.1:8765 运行并托管 web/dist。
 * 运行：python scripts/e2e/run_browser_e2e.py browser_e2e_m29.cjs
 * 环境变量：DATAMASON_E2E_USER / DATAMASON_E2E_PASS（默认 laomao / pw）
 */
const { chromium } = require("playwright");

const BASE = process.env.E2E_BASE || "http://127.0.0.1:8765";
const LIMIT_MS = 300_000; // ≤5 分钟
const STAMP = String(Date.now() % 1_000_000);
const SHOT_DIR = process.env.E2E_SHOT_DIR || ".workbuddy/tmp";
const HEADLESS = process.env.E2E_HEADED !== "1";
const E2E_USER = process.env.DATAMASON_E2E_USER || "laomao";
const E2E_PASS = process.env.DATAMASON_E2E_PASS || "pw";
const OP_USER = `xiaoli${STAMP}`;
const OP_PASS = "pw";
const CREATE_BIZ = `协作客户系统${STAMP}`;   // 创建模式部署（向导建）
const LINK_BIZ = `外部产品系统${STAMP}`;     // 直连模式部署（向导建）
const SUPPLIER_NAME = `华东供应商${STAMP}`;
const PRODUCT_NAME = `直连产品${STAMP}`;
const adminName = `老猫客户${STAMP}`;
const opName = `小李客户${STAMP}`;

const log = (...a) => console.log("[e2e]", ...a);
const btn = (label) => new RegExp(label.split("").join("\\s*"));
const failures = [];
const t0 = Date.now();
const elapsed = () => ((Date.now() - t0) / 1000).toFixed(1) + "s";

async function shot(page, name) {
  const p = `${SHOT_DIR}/e2e_m29_${name}.png`;
  await page.screenshot({ path: p, fullPage: true }).catch(() => {});
  log("screenshot:", p);
}

async function pickOption(page, text) {
  const dropdown = page.locator(".ant-select-dropdown:visible");
  const opt = dropdown.locator(".ant-select-item-option", { hasText: text }).first();
  await opt.waitFor({ timeout: 10000 });
  await opt.click();
}

// 带登录 cookie 的 page 上直接调用 API（统一 /api/v1 前缀，与 SPA 同源）
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
  await page.goto(BASE + "/login", { waitUntil: "domcontentloaded", timeout: 40000 });
  const r = await api(page, "/auth/login", { method: "POST", body: { username: user, password: pass } });
  if (r.status !== 200) throw new Error("login-failed " + r.status);
  await page.goto(BASE + "/deployments", { waitUntil: "domcontentloaded", timeout: 40000 });
  await page.getByText("我部署过的系统").first().waitFor({ timeout: 20000 });
  log(`已登录 ${user}`, elapsed());
}

function loginIntoWork(appId, user, pass) {
  return async (page) => {
    await page.goto(BASE + "/login", { waitUntil: "domcontentloaded", timeout: 40000 });
    const r = await api(page, "/auth/login", { method: "POST", body: { username: user, password: pass } });
    if (r.status !== 200) throw new Error("login-failed " + r.status);
    await page.goto(BASE + `/app/${appId}`, { waitUntil: "domcontentloaded", timeout: 40000 });
    await page.getByText(/我的记录/).first().waitFor({ timeout: 20000 }).catch(async (e) => {
      const dump = await page.evaluate(() => ({
        url: location.href,
        title: document.title,
        text: document.body ? document.body.innerText.slice(0, 200) : "NB",
        html: document.body ? document.body.innerHTML.slice(0, 300) : "NB",
      })).catch(() => null);
      console.error("OP-WS-STATE", JSON.stringify(dump));
      throw e;
    });
    log(`已登录 ${user} 进入工作台`, elapsed());
  };
}

async function logout(page) {
  if (page.url().includes("/app/")) {
    await page.goto(BASE + "/deployments", { waitUntil: "domcontentloaded", timeout: 40000 });
  }
  const r = await api(page, "/auth/logout", { method: "POST" });
  if (r.status !== 200) throw new Error("logout-failed " + r.status);
  await page.goto(BASE + "/login", { waitUntil: "domcontentloaded", timeout: 40000 });
  await page.getByPlaceholder("密码").waitFor({ timeout: 15000 });
  log("已退出", elapsed());
}

async function openWorkspace(page, cardText) {
  // M2-9：经 API 取 entry_path 后全量 goto，规避 werkzeug dev-server 长连接后
  // SPA 客户端路由导航偶发白屏（M2-5 已知怪癖；直连 goto 稳定）。
  const list = await api(page, "/deployments");
  const d = list.data?.find?.((x) => String(x.biz_name).includes(cardText));
  if (!d || !d.entry_path) throw new Error("未找到可打开的工作台: " + cardText);
  await page.goto(BASE + d.entry_path, { waitUntil: "domcontentloaded", timeout: 40000 });
}

async function clickMenu(page, text) {
  await page.locator(".ant-menu-item", { hasText: text }).click();
}

// 用全局搜索命中唯一记录（避免演示种子/跨轮次数据干扰断言）
async function searchRowVisible(page, text) {
  const searchInput = page.getByPlaceholder("全局搜索");
  await searchInput.fill(text);
  await searchInput.press("Enter");
  await page.getByText(text).first().waitFor({ timeout: 15000 });
  await searchInput.fill("");
  await searchInput.press("Enter");
}

// 关闭就绪弹窗：antd 两字按钮自动插空格（“完 成”），轮询用 DOM click
async function dismissDoneModal(page) {
  let dismissed = false;
  for (let i = 0; i < 20; i++) {
    const clicked = await page.evaluate(() => {
      const btns = Array.from(document.querySelectorAll(".ant-modal button"));
      const b = btns.find((x) => (x.textContent || "").replace(/\s+/g, "") === "完成");
      if (b) { b.click(); return true; }
      return false;
    });
    if (clicked) {
      try {
        await page.waitForFunction(
          () => !document.querySelector(".ant-modal-wrap:not([style*='display: none']) .ant-modal"),
          { timeout: 3000 }
        );
        dismissed = true;
        break;
      } catch { /* 再试 */ }
    }
    await page.waitForTimeout(300);
  }
  if (!dismissed) throw new Error("未能关闭就绪弹窗");
}

(async () => {
  const browser = await chromium.launch({
    headless: HEADLESS,
    args: ["--no-proxy-server"],
  });
  const context = await browser.newContext({ viewport: { width: 1500, height: 1000 } });
  const page = await context.newPage();
  const crashes = [];
  page.on("pageerror", (e) => {
    crashes.push(String(e).slice(0, 400));
    log("pageerror:", String(e).slice(0, 300));
  });

  try {
    // -------- 1. 创建人：登录 + 建操作员账号 --------
    await login(page, E2E_USER, E2E_PASS);
    const mk = await api(page, "/auth/users", {
      method: "POST",
      body: { username: OP_USER, display_name: "小李", password: OP_PASS },
    });
    if (mk.status !== 201) throw new Error("创建用户失败: " + JSON.stringify(mk.data));
    const OP_ID = mk.data.id;
    log("操作员账号已建", mk.data.username, "id", OP_ID, elapsed());

    // -------- 2. 创建人：部署向导（创建模式，LLM 桩）→ 就绪 --------
    await page.getByRole("button", { name: "部署一套系统" }).click();
    await page.getByPlaceholder(/做个留言板/).fill("客户和订单管理，方便跟进客户、登记订单与说明");
    await page.getByPlaceholder("给这套系统起个名").fill(CREATE_BIZ);
    // 不勾演示数据：stub 结构 customers.name 唯一，确定性种子 10 行会撞名（UNIQUE 冲突），
    // 且本场景对记录计数用自建行断言即可
    await page.locator(".ant-checkbox-wrapper", { hasText: "就绪即带演示数据" }).click();
    await page.getByRole("button", { name: btn("生成提案") }).click();
    await page.getByRole("button", { name: btn("就这么部署") }).waitFor({ timeout: 30000 });
    await page.getByText("数据关系（简版）").waitFor({ timeout: 15000 });
    const codeCount = await page.locator(".ant-modal code").count();
    if (codeCount < 2) throw new Error("提案中未见表字段 code 标记: " + codeCount);
    if ((await page.locator(".ant-modal", { hasText: "customers" }).count()) < 1) {
      throw new Error("提案应含 customers");
    }
    if ((await page.locator(".ant-modal", { hasText: "orders" }).count()) < 1) {
      throw new Error("提案应含 orders");
    }
    log("创建模式提案已生成（ER 缩略图 + 双表）", elapsed());
    await shot(page, "01_create_proposal");

    await page.getByRole("button", { name: btn("就这么部署") }).click();
    await page.getByText("系统已就绪").waitFor({ timeout: 60000 });
    log("创建模式部署就绪", elapsed());
    await dismissDoneModal(page);
    await page.getByText(CREATE_BIZ).first().waitFor({ timeout: 15000 });

    const dep = await api(page, "/deployments");
    const d = dep.data.find((x) => String(x.biz_name) === CREATE_BIZ);
    if (!d || d.status !== "running" || !d.entry_path) {
      throw new Error("未找到新部署: " + JSON.stringify(dep.data?.map?.((x) => x.biz_name)));
    }
    const DEP_ID = d.id;
    log("创建模式部署", DEP_ID, d.entry_path, elapsed());

    // -------- 3. 创建人：工作台 → 加成员（操作员 · 仅授权 客户管理）--------
    const rAdmin = await api(page, `/app/${DEP_ID}/api/tables/customers/rows`, {
      method: "POST", body: { name: adminName, phone: "13900000001" },
    });
    if (rAdmin.status !== 201) throw new Error("创建人创建客户失败: " + JSON.stringify(rAdmin.data));

    await openWorkspace(page, CREATE_BIZ);
    await page.locator(".ant-menu-item", { hasText: "成员与权限" }).waitFor({ timeout: 15000 });
    await clickMenu(page, "成员与权限");
    await page.getByText("成员与权限 ·", { exact: false }).first().waitFor({ timeout: 15000 });

    await page.getByRole("button", { name: btn("添加成员") }).click();
    const modal = page.locator(".ant-modal-content").last();
    await modal.waitFor();
    await modal.locator(".ant-select", { hasText: "选择账号" }).first().click();
    // 历史累积用户多：先输入账号名过滤（optionFilterProp=label），再选唯一行
    await page.keyboard.type(OP_USER);
    await pickOption(page, `小李（${OP_USER}）`);
    const treeNode = modal.locator(".ant-tree-treenode", { hasText: "客户管理" }).first();
    await treeNode.waitFor({ timeout: 10000 });
    await treeNode.locator(".ant-tree-checkbox").first().click();
    await shot(page, "02_grant_modal");
    await modal.getByRole("button", { name: btn("添加") }).click();
    await page.locator(".ant-message").getByText("成员已添加").first().waitFor({ timeout: 15000 });
    const memberRow = page.locator(".ant-table-tbody tr.ant-table-row", { hasText: `@${OP_USER}` }).first();
    await memberRow.waitFor({ timeout: 10000 });
    const memberCell = await memberRow.innerText();
    if (!memberCell.includes("操作员")) throw new Error("成员行应为操作员: " + memberCell);
    if (!memberCell.includes("客户管理")) throw new Error("成员分工应含客户管理: " + memberCell);
    log("操作员已添加并仅授权「客户管理」", elapsed());
    await shot(page, "03_member_added");

    // 创建人的记录在工作台可见
    await clickMenu(page, "客户管理");
    await searchRowVisible(page, adminName);
    log("创建人记录可见", elapsed());

    // -------- 4. 双角色切换：操作员登录 → 只见授权菜单 + 我的记录 --------
    await loginIntoWork(DEP_ID, OP_USER, OP_PASS)(page);
    const rOp = await api(page, `/app/${DEP_ID}/api/tables/customers/rows`, {
      method: "POST", body: { name: opName, phone: "13900000002" },
    });
    if (rOp.status !== 201) throw new Error("操作员创建客户失败: " + JSON.stringify(rOp.data));
    await page.reload({ waitUntil: "domcontentloaded", timeout: 40000 });
    await page.getByText(/我的记录/).first().waitFor({ timeout: 15000 });

    if ((await page.locator(".ant-menu-item", { hasText: "订单管理" }).count()) > 0) {
      throw new Error("操作员不应看到「订单管理」菜单");
    }
    if ((await page.locator(".ant-menu-item", { hasText: "成员与权限" }).count()) > 0) {
      throw new Error("操作员不应看到「成员与权限」");
    }
    if ((await page.locator(".ant-menu-item", { hasText: "操作日志" }).count()) > 0) {
      throw new Error("操作员不应看到「操作日志」");
    }
    await page.getByText(opName).first().waitFor({ timeout: 10000 });
    await page.waitForFunction(
      (t) => !document.body.innerText.includes(t), adminName,
      { timeout: 10000 },
    );
    await page.getByText("共 1 条").waitFor({ timeout: 10000 });
    log("操作员只见授权菜单与自己的记录", elapsed());
    await shot(page, "04_operator_mine");

    // 操作员日志被服务端强制为只见自己
    const opLogs = await api(page, `/app/${DEP_ID}/api/activity-logs`);
    const opRows = Array.isArray(opLogs.data?.logs) ? opLogs.data.logs : [];
    if (String(opLogs.data?.total) !== "1") {
      throw new Error("操作员日志应只有自己 1 条: " + JSON.stringify(opLogs.data));
    }
    if (!opRows.every((g) => g.actor_user_id === OP_ID && g.action === "create")) {
      throw new Error("操作员日志 actor 应全为自己: " + JSON.stringify(opLogs.data));
    }
    log("操作员日志只见自己（服务端强制）", elapsed());

    // -------- 5. 创建人复查：全量记录 + 全量日志（含操作员）--------
    await login(page, E2E_USER, E2E_PASS);
    await openWorkspace(page, CREATE_BIZ);
    await clickMenu(page, "客户管理");
    await searchRowVisible(page, opName);
    await searchRowVisible(page, adminName);
    log("创建人全量记录（含操作员录入）", elapsed());

    await clickMenu(page, "操作日志");
    await page.locator(".ant-table").first().waitFor({ timeout: 15000 });
    await page.getByText(opName).first().waitFor({ timeout: 15000 });
    await page.getByText(adminName).first().waitFor({ timeout: 10000 });
    log("创建人全量日志（含操作员的记录摘要）", elapsed());
    await shot(page, "05_admin_logs");

    // -------- 6. 直连模式：接已有的库 → 提案 → 部署（不依赖 LLM）--------
    await page.getByRole("button", { name: btn("返回控制台") }).click();
    await page.getByText("我部署过的系统").first().waitFor({ timeout: 15000 });
    await page.getByRole("button", { name: "部署一套系统" }).click();
    await page.locator(".ant-segmented-item", { hasText: "接已有的库" }).click();

    await page.locator(".ant-select", { hasText: "选择要接入的已有连接" }).first().click();
    await pickOption(page, "E2E外部产品库（sqlite）");
    const tableSelect = page.locator(".ant-select", { hasText: "选择要接入的表" }).first();
    await tableSelect.waitFor({ timeout: 15000 });
    await tableSelect.click();
    await pickOption(page, "suppliers");
    await pickOption(page, "products");

    await page.getByPlaceholder(/接已有的客户订单库/).fill("接已有的产品库做产品与供应商管理");
    await page.getByPlaceholder("给这套系统起个名").fill(LINK_BIZ);
    await page.getByRole("button", { name: btn("生成提案") }).click();
    await page.getByRole("button", { name: btn("就这么部署") }).waitFor({ timeout: 30000 });
    if ((await page.locator(".ant-modal", { hasText: "products" }).count()) < 1) {
      throw new Error("直连提案应含 products");
    }
    if ((await page.locator(".ant-modal", { hasText: "suppliers" }).count()) < 1) {
      throw new Error("直连提案应含 suppliers");
    }
    await page.getByText(/缺少归属列 created_by/).first().waitFor({ timeout: 10000 });
    log("直连提案已生成（真实反射 + 降级明示）", elapsed());
    await shot(page, "06_link_proposal");

    await page.getByRole("button", { name: btn("就这么部署") }).click();
    await page.getByText("系统已就绪").waitFor({ timeout: 60000 });
    await dismissDoneModal(page);
    await page.getByText(LINK_BIZ).first().waitFor({ timeout: 15000 });

    const dep2 = await api(page, "/deployments");
    const ld = dep2.data.find((x) => String(x.biz_name) === LINK_BIZ);
    if (!ld || ld.status !== "running" || ld.data_mode !== "link") {
      throw new Error("未找到直连部署: " + JSON.stringify(ld) + " / " +
        JSON.stringify(dep2.data?.map?.((x) => [x.biz_name, x.data_mode])));
    }
    const LINK_ID = ld.id;
    log("直连部署", LINK_ID, ld.entry_path, elapsed());

    // -------- 7. 直连部署出 CRUD：先建供应商，再在工作台 UI 新增产品（外键下拉）--------
    const rSup = await api(page, `/app/${LINK_ID}/api/tables/suppliers/rows`, {
      method: "POST", body: { name: SUPPLIER_NAME, region: "上海" },
    });
    if (rSup.status !== 201) throw new Error("创建供应商失败: " + JSON.stringify(rSup.data));

    await openWorkspace(page, LINK_BIZ);
    await page.locator(".ant-menu-item", { hasText: "products" }).waitFor({ timeout: 15000 });
    await clickMenu(page, "products");
    await page.waitForFunction(() => {
      const t = document.querySelector(".ant-table-thead")?.innerText || "";
      return t.includes("price");
    }, { timeout: 15000 });

    await page.getByRole("button", { name: btn("新增") }).first().click();
    const drawer = page.locator(".ant-drawer-content");
    await drawer.waitFor();
    await drawer.getByLabel("name").fill(PRODUCT_NAME);
    await drawer.getByLabel("price").fill("88.50");
    await drawer.getByLabel("supplier_id").click();
    await pickOption(page, SUPPLIER_NAME);
    await drawer.getByRole("button", { name: btn("新增") }).click();
    await page.locator(".ant-message").getByText("已新增").first().waitFor({ timeout: 15000 });
    await page.getByText(PRODUCT_NAME).first().waitFor({ timeout: 10000 });
    log("直连外部库经 UI 新增产品成功（FK 下拉）", elapsed());
    await shot(page, "07_link_crud_created");

    if (crashes.length) throw new Error("会话出现页面异常: " + crashes.join(" | "));
  } catch (e) {
    const state = await page.evaluate(() => ({
      url: location.href,
      text: document.body ? document.body.innerText.slice(0, 600) : "",
    })).catch(() => ({}));
    failures.push("异常: " + (e && e.message ? e.message : String(e)));
    if (state && state.text) failures.push("页面状态: " + JSON.stringify(state));
    const p = `${SHOT_DIR}/e2e_m29_99_error.png`;
    await page.screenshot({ path: p, fullPage: true }).catch(() => {});
  } finally {
    await browser.close();
  }

  const total = Date.now() - t0;
  log("总耗时", (total / 1000).toFixed(1) + "s", `限额 ${LIMIT_MS / 1000}s`);
  if (total > LIMIT_MS) failures.push(`总耗时 ${total}ms 超过 5 分钟限额`);

  if (failures.length) {
    console.error("BROWSER_E2E_M29_FAIL");
    for (const f of failures) console.error(" -", f);
    process.exit(1);
  }
  console.log("BROWSER_E2E_M29_OK");
  process.exit(0);
})();