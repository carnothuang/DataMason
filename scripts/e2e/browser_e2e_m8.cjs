/* M8 真浏览器 E2E 验收（Playwright / Chromium）：0.14.0 纯手动专业模式（SQL + 可视化）。
 * 剧本（对 0.14.0 版本规划 §六 真实浏览器验收线逐条）：
 *   ① 首页「部署一套系统」下拉三入口并存（全自动 / 混合引导 / 纯手动专业模式）
 *   ② 专业模式写「客户 / 订单 / 商品」多表含外键 SQL → 解析 → 可视化即时呈现表与连线
 *   ③ 可视化改一列类型、把一列标记为 FILE、删 / 加一条连线 → 反生成 SQL 对应变化
 *   ④ 故意写不支持类型（JSON）与悬空外键 → 错误定位到表 / 列，修正后解析通过
 *   ⑤ 未配置任何 LLM 的情况下成功部署 → 工作台菜单出现、可录数据
 *   ⑥ 直连模式不出现专业模式入口（DR-45 红线，接口层面 mode=link 直接拒绝）
 * 前置：serve_for_e2e.py 在 127.0.0.1:8765 运行、托管 dashboard/dist；不设 DATAMASON_LLM_* 即天然无 LLM。
 * 运行：python scripts/e2e/run_browser_e2e.py browser_e2e_m8.cjs
 */
const { chromium } = require("playwright");

const BASE = process.env.E2E_BASE || "http://127.0.0.1:8765";
const LIMIT_MS = 300_000;
const STAMP = String(Date.now() % 1_000_000);
const SHOT_DIR = process.env.E2E_SHOT_DIR || ".workbuddy/tmp";
const HEADLESS = process.env.E2E_HEADED !== "1";
const E2E_USER = process.env.DATAMASON_E2E_USER || "laomao";
const E2E_PASS = process.env.DATAMASON_E2E_PASS || "pw";
const BIZ = `M8专业E2E${STAMP}`;

const log = (...a) => console.log("[e2e]", ...a);
const btn = (label) => new RegExp(label.split("").join("\\s*"));

async function shot(page, name) {
  const p = `${SHOT_DIR}/e2e_m8_${name}.png`;
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
    // 滑块验证码：监听 window mousemove/mouseup，拖到 >=92% 即通过
    const track = page.getByTestId("slide-captcha-track");
    const thumb = page.getByTestId("slide-captcha-thumb");
    await track.waitFor({ timeout: 8000 });
    const tb = await track.boundingBox();
    const hb = await thumb.boundingBox();
    if (tb && hb) {
      const sx = hb.x + hb.width / 2;
      const sy = hb.y + hb.height / 2;
      const distance = tb.width - hb.width - 2;
      await page.mouse.move(sx, sy);
      await page.mouse.down();
      const steps = 12;
      for (let i = 1; i <= steps; i += 1) {
        await page.mouse.move(sx + (distance * i) / steps, sy, { steps: 2 });
        await page.waitForTimeout(15);
      }
      await page.mouse.up();
      await page.waitForTimeout(200);
    }
    await page.getByRole("button", { name: /登\s*录/ }).first().click();
  }
  await heading.waitFor({ timeout: 30000 });
}

const msg = (page, text) => page.getByText(text, { exact: false });

// 三表含外键：customers / products / orders（orders 同时引用客户与商品）。
const MULTI_SQL = `CREATE TABLE customers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name VARCHAR(100) NOT NULL,
  phone VARCHAR(30) UNIQUE
);

CREATE TABLE products (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title VARCHAR(120) NOT NULL,
  price DECIMAL(10,2) NOT NULL
);

CREATE TABLE orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  customer_id INTEGER REFERENCES customers(id),
  product_id INTEGER REFERENCES products(id),
  amount DECIMAL(10,2) NOT NULL,
  note VARCHAR(200)
);`;

// 故意出错之一：orders 列用 JSON（不支持类型）。
const BAD_JSON_SQL = `CREATE TABLE customers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name VARCHAR(100) NOT NULL
);

CREATE TABLE orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  customer_id INTEGER REFERENCES customers(id),
  payload JSON
);`;

// 故意出错之二：引用不存在的表 logistics（悬空外键）。
const BAD_FK_SQL = `CREATE TABLE customers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name VARCHAR(100) NOT NULL
);

CREATE TABLE orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  customer_id INTEGER REFERENCES customers(id),
  ship_id INTEGER REFERENCES logistics(id)
);`;

// 在右侧字段表里把指定字段的类型 Select 切到目标选项。
async function pickType(page, fieldName, optionText) {
  const row = page.locator("tr.ant-table-row", {
    has: page.locator(`input[value="${fieldName}"]`),
  }).first();
  await row.waitFor({ timeout: 10000 });
  await row.scrollIntoViewIfNeeded();
  const selects = row.locator(".ant-select");
  const n = await selects.count();
  let target = null;
  for (let i = 0; i < n; i += 1) {
    const txt = (await selects.nth(i).textContent() || "").replace(/\s+/g, "").trim();
    const known = ["整数INTEGER", "长整数BIGINT", "短文本VARCHAR", "长文本TEXT", "布尔BOOLEAN", "日期时间DATETIME", "金额DECIMAL", "附件FILE", "VARCHAR", "TEXT", "INTEGER", "BIGINT", "BOOLEAN", "DATETIME", "DECIMAL", "FILE"];
    if (known.includes(txt)) { target = selects.nth(i); break; }
  }
  if (!target) throw new Error(`pickType 未找到 ${fieldName} 的类型 Select`);
  await target.click();
  const opt = page
    .locator(".ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option")
    .filter({ hasText: optionText })
    .first();
  await opt.waitFor({ timeout: 8000 });
  await opt.click();
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

    // ① 首页下拉三入口并存
    await page.getByRole("button", { name: /部\s*署\s*一\s*套\s*系\s*统/ }).first().click();
    const menu = page.locator(".ant-dropdown-menu").last();
    await menu.waitFor({ timeout: 8000 });
    const hasAuto = await menu.getByText("全自动（简单业务）").count();
    const hasHybrid = await menu.getByText("混合引导（复杂需求）").count();
    const hasManual = await menu.getByText("纯手动 · 专业模式").count();
    check(hasAuto && hasHybrid && hasManual, "① 三种建库入口并存");
    await menu.getByText("纯手动 · 专业模式").click();

    const modal = page.locator(".ant-modal").filter({ hasText: "纯手动建库" }).last();
    await modal.waitFor({ timeout: 10000 });
    check(true, "① 打开纯手动专业模式工作台");

    // ② 写多表含外键 SQL → 解析 → 可视化呈现表与连线
    await modal.locator("textarea").first().fill(MULTI_SQL);
    await modal.getByRole("button", { name: btn("解析为结构") }).click();
    await msg(page, "已解析 3 张表").first().waitFor({ timeout: 15000 });
    check(true, "② 多表含外键 SQL 解析成功（3 张表）");
    await page.waitForTimeout(500);
    const nodeCount = await modal.locator("svg g > rect").count();
    check(nodeCount === 3, `② 可视化呈现 3 个表节点（${nodeCount}）`);
    const edgeCount = await modal.locator("svg line").count();
    check(edgeCount === 2, `② 可视化呈现 2 条外键连线（${edgeCount}）`);
    await shot(page, "01_parsed_multi");

    // ③ 可视化改类型 / 标 FILE / 删连线 / 加连线 → 反生成 SQL 对应变化
    // 选中 orders 表（点节点表头文本），在右侧字段表编辑
    await modal.locator("svg text").filter({ hasText: "orders" }).first().click();
    await page.waitForTimeout(300);
    // note VARCHAR → 长文本 TEXT
    await pickType(page, "note", "长文本 TEXT");
    // amount DECIMAL → 附件 FILE（验证 FILE 仅可视化可标）
    await pickType(page, "amount", "附件 FILE");
    // 删除 orders.product_id → products 的连线（点连线上的「点击删除」文本）
    const delEdgeLabel = modal.locator("svg text").filter({ hasText: "product_id · 点击删除" }).first();
    await delEdgeLabel.waitFor({ timeout: 8000 });
    await delEdgeLabel.evaluate((el) => {
      el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
    });
    let confirmBox = page.locator(".ant-modal-confirm").filter({ hasText: "删除这条关系？" }).last();
    await confirmBox.waitFor({ timeout: 8000 });
    await confirmBox.getByRole("button", { name: btn("删除") }).click();
    await page.waitForTimeout(400);
    const edgesAfterDel = await modal.locator("svg line").count();
    check(edgesAfterDel === 1, `③ 删除一条连线后剩 1 条（${edgesAfterDel}）`);

    // 重新加回连线：orders 节点「连线」→ 点 products 节点表头 → 建立关系
    // 小画布环形布局节点几何重叠，且多层 has 过滤器在 SVG 内不稳；
    // 直接定位 SVG text（节点操作顺序与表顺序一致：customers/products/orders），用 dispatchEvent 触发，绕过命中测试
    const clickViaDispatch = async (loc) => {
      await loc.waitFor({ timeout: 8000 });
      await loc.evaluate((el) => {
        el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
      });
    };
    const linkLabels = modal.locator("svg text").filter({ hasText: /^连线$/ });
    await clickViaDispatch(linkLabels.nth(2));
    await page.waitForTimeout(200);
    const productsHeader = modal.locator("svg text").filter({ hasText: /^products$/ }).first();
    await clickViaDispatch(productsHeader);
    confirmBox = page.locator(".ant-modal-confirm").filter({ hasText: "建立关系" }).last();
    await confirmBox.waitFor({ timeout: 8000 });
    await confirmBox.getByRole("button", { name: btn("建立关系") }).click();
    await page.waitForTimeout(400);
    const edgesAfterAdd = await modal.locator("svg line").count();
    check(edgesAfterAdd === 2, `③ 加回连线后为 2 条（${edgesAfterAdd}）`);

    // 反生成 SQL，核对类型 / FILE / 外键变化
    await modal.getByRole("button", { name: btn("按结构生成 SQL") }).click();
    confirmBox = page.locator(".ant-modal-confirm").filter({ hasText: "按当前结构重排 SQL" }).last();
    await confirmBox.waitFor({ timeout: 8000 });
    await confirmBox.getByRole("button", { name: btn("重排 SQL") }).click();
    await msg(page, "已按草稿重新生成 SQL").first().waitFor({ timeout: 8000 });
    const sqlValue = await modal.locator("textarea").first().inputValue();
    check(/\bnote\s+TEXT\b/.test(sqlValue), "③ 反生成 SQL：note 已改为 TEXT");
    check(/\bamount\s+VARCHAR\b/.test(sqlValue), "③ 反生成 SQL：FILE 以 VARCHAR 承载");
    check(/products_id\s+INTEGER\s+REFERENCES\s+products/.test(sqlValue), "③ 反生成 SQL：连线恢复（新列 products_id 引用 products）");
    const ordersSeg = (sqlValue.match(/CREATE TABLE orders[\s\S]*?\);/) || [""])[0];
    check(!/DECIMAL/.test(ordersSeg), "③ 反生成 SQL：orders 段原 DECIMAL 已不存在");
    check(/DECIMAL/.test(sqlValue), "③ 反生成 SQL：未改的 products.price 保留 DECIMAL");
    await shot(page, "02_visual_changes_sql");

    // ④ 故意 JSON 与悬空外键 → 分别定位报错，修正后通过
    // 两类错误在解析阶段先后短路，无法在同一次解析中同时出现，故拆成两次独立解析
    const parseAndGetAlert = async (sqlSrc) => {
      await modal.locator("textarea").first().fill(sqlSrc);
      await modal.getByRole("button", { name: btn("解析为结构") }).click();
      const errAlert = modal.locator(".ant-alert-error").filter({ hasText: "校验未通过" }).last();
      await errAlert.waitFor({ timeout: 15000 });
      return errAlert.innerText();
    };
    const jsonAlertText = await parseAndGetAlert(BAD_JSON_SQL);
    check(/JSON/.test(jsonAlertText) && /orders/.test(jsonAlertText), "④ 不支持类型 JSON 定位到 orders");

    const fkAlertText = await parseAndGetAlert(BAD_FK_SQL);
    check(/logistics/.test(fkAlertText), "④ 悬空外键定位到不存在的表 logistics");
    await shot(page, "03_error_located");

    // 修正：JSON → VARCHAR，去掉悬空外键列
    const fixedSql = BAD_FK_SQL
      .replace("ship_id INTEGER REFERENCES logistics(id)", "payload VARCHAR(500)");
    await modal.locator("textarea").first().fill(fixedSql);
    await modal.getByRole("button", { name: btn("解析为结构") }).click();
    await msg(page, "已解析 2 张表").first().waitFor({ timeout: 15000 });
    check(true, "④ 修正后解析通过（2 张表）");

    // ⑤ 无 LLM 配置下成功部署（全程未设 DATAMASON_LLM_*）
    // 先证明当前确实没有任何 LLM 模型
    const models = await api(page, "/settings/models");
    const modelCount = Array.isArray(models.data) ? models.data.length : 0;
    check(modelCount === 0, `⑤ 部署前确认无 LLM 模型配置（${modelCount} 个）`);

    // 重新载入完整三表 SQL（上一步只解析了两表演示错误修正），保证最终结构完整
    await modal.locator("textarea").first().fill(MULTI_SQL);
    await modal.getByRole("button", { name: btn("解析为结构") }).click();
    await msg(page, "已解析 3 张表").first().waitFor({ timeout: 15000 });
    // 应用可视化的 note→TEXT、amount→FILE 标记，再部署
    await modal.locator("svg text").filter({ hasText: "orders" }).first().click();
    await page.waitForTimeout(300);
    await pickType(page, "note", "长文本 TEXT");
    await pickType(page, "amount", "附件 FILE");

    await modal.getByPlaceholder("给这套系统起个名").fill(BIZ);
    // 默认不带演示数据（Q2）：确认「带演示数据」勾选框未勾
    const seedChecked = await modal.locator("input[type=checkbox]").last().isChecked();
    check(!seedChecked, "⑤ 专业模式默认不勾选演示种子");
    await shot(page, "04_ready_deploy");
    await modal.getByRole("button", { name: btn("校验并部署") }).click();

    await page.getByText("系统已就绪").first().waitFor({ timeout: 30000 });
    check(true, "⑤ 无 LLM 部署成功，进入就绪态");
    await shot(page, "05_deployed");

    // 工作台菜单出现、可录数据
    const depList = await api(page, "/deployments");
    const dep = Array.isArray(depList.data)
      ? depList.data.find((x) => x.biz_name === BIZ) : null;
    check(!!dep, "⑤ 新部署出现在控制台清单");
    const did = dep && dep.id;
    const entry = (dep && dep.entry_path) || `/app/${did}`;

    await page.goto(BASE + entry, { waitUntil: "domcontentloaded" });
    await page.locator(".dm-nav-item").first().waitFor({ timeout: 20000 });
    await page.waitForTimeout(800);
    const menuCustomers = await page.locator(".dm-nav-item__label").filter({ hasText: "customers" }).count();
    const menuOrders = await page.locator(".dm-nav-item__label").filter({ hasText: "orders" }).count();
    check(menuCustomers && menuOrders, "⑤ 工作台菜单出现业务表入口");

    // 录一条客户数据（走真实 CRUD）
    await page.locator(".dm-nav-item").filter({ hasText: "customers" }).first().click();
    await page.waitForTimeout(600);
    await page.getByRole("button", { name: /新\s*增/ }).first().click();
    const drawer = page.locator(".ant-drawer").filter({ hasText: "customers" }).last();
    await drawer.waitFor({ timeout: 10000 });
    const nameInput = drawer.locator("input#name, input[id$='name']").first();
    await nameInput.fill(`客户${STAMP}`);
    await drawer.getByRole("button", { name: /^新\s*增$/ }).first().click();
    await msg(page, "已新增").first().waitFor({ timeout: 15000 });
    check(true, "⑤ 工作台可录入数据");
    await shot(page, "06_record_created");

    // ⑥ 直连红线：解析接口 mode=link 直接拒绝
    const linkParse = await api(page, "/deployments/manual/sql/parse", {
      method: "POST",
      body: { sql: MULTI_SQL, dialect: "sqlite", mode: "link" },
    });
    check(linkParse.status === 400, `⑥ 直连模式拒绝专业建库（${linkParse.status}）`);
    check(
      !!(linkParse.data && /直连外部库只读/.test(linkParse.data.error || "")),
      "⑥ 拒绝原因为直连只读红线（DR-45）",
    );
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
