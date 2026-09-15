/* M2-4 真浏览器 E2E（Playwright / Chromium）。
 * 验收线：登录 → 进工作台 → 客户列表（新增/搜索/筛选/编辑/删除）→ 订单页外键关联下拉新增。
 * 前置：scripts/e2e/serve_for_e2e.py 已在 127.0.0.1:8765 运行、托管 web/dist，且已预置
 *       确定性演示部署「客户与订单E2E」（spec 固定、data 为空）。不依赖真实 LLM。
 * 运行：见 scripts/e2e/run_browser_e2e.py（NODE_PATH 指向全局 playwright）。
 * 环境变量：
 *   DATAMASON_E2E_USER / DATAMASON_E2E_PASS（默认 laomao / pw，与 M1-6 一致）
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

// antd 对两个字符的按钮会渲染为「新 增」，用正则容忍任意空格
const btn = (label) => new RegExp(label.split("").join("\\s*"));

// 清空当前列表（删除残留数据），保证精确计数断言可重跑
async function clearTable(page) {
  for (let i = 0; i < 100; i++) {
    const rows = page.locator(".ant-table-tbody tr.ant-table-row");
    if ((await rows.count()) === 0) break;
    const first = rows.first();
    await first.getByRole("button", { name: btn("删除") }).click();
    await page.locator(".ant-popconfirm").getByRole("button", { name: btn("删除") }).click();
    await page.locator(".ant-message").getByText("已删除").first().waitFor({ timeout: 10000 });
    await page.waitForTimeout(250);
  }
  await page.waitForFunction(
    () => document.querySelectorAll(".ant-table-tbody tr.ant-table-row").length === 0,
    { timeout: 10000 },
  );
}

async function shot(page, name) {
  const p = `${SHOT_DIR}/e2e_m24_${name}.png`;
  await page.screenshot({ path: p, fullPage: true }).catch(() => {});
  log("screenshot:", p);
}

// antd Select 下拉：点开触发器后，选中可见下拉里文案匹配的 option
async function pickOption(page, text) {
  const dropdown = page.locator(".ant-select-dropdown:visible");
  const opt = dropdown.locator(".ant-select-item-option", { hasText: text }).first();
  await opt.waitFor({ timeout: 10000 });
  await opt.click();
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
  const uniqueName = `老猫${STAMP}`;
  const otherName = `喵星${STAMP}`;

  const expectRowText = async (text) => {
    await page.locator(".ant-table-tbody").getByText(text, { exact: false }).first().waitFor();
  };

  try {
    // 0. 登录（成功后首页出现「我部署过的系统」才算数）
    await page.goto(BASE + "/", { waitUntil: "networkidle" });
    if ((await page.getByRole("button", { name: /登\s*录/ }).count()) > 0) {
      await page.getByPlaceholder("用户名").fill(E2E_USER);
      await page.getByPlaceholder("密码").fill(E2E_PASS);
      await page.getByRole("button", { name: /登\s*录/ }).click();
    }
    await page.getByText("我部署过的系统").first().waitFor({ timeout: 15000 });
    log("已登录", elapsed());

    // 1. 取预置部署入口（列表 API 拿 entry_path，避免猜 id）
    const dep = await page.evaluate(async (base) => {
      const r = await fetch(base + "/api/v1/deployments");
      const body = await r.json();
      if (!Array.isArray(body)) return { error: `status=${r.status} body=${JSON.stringify(body)}` };
      const d = body.find((x) => String(x.biz_name).startsWith("客户与订单E2E"));
      return d ? { entry_path: d.entry_path, id: d.id } : {
        error: "未找到「客户与订单E2E」，现有: " + body.map((x) => x.biz_name).join(","),
      };
    }, BASE);
    if (!dep.entry_path) throw new Error("取部署失败: " + (dep.error || "未知"));
    log("演示部署 entry_path:", dep.entry_path, elapsed());

    // 2. 进工作台 → 客户列表（首个叶子菜单自动选中）
    await page.goto(BASE + dep.entry_path, { waitUntil: "networkidle" });
    await page.getByRole("button", { name: btn("新增") }).first().waitFor({ timeout: 15000 });
    // 等列表真正加载（出现客户的标志列），清残留前避免空表过早 break
    await page.waitForFunction(() => {
      const t = document.querySelector(".ant-table-thead")?.innerText || "";
      return t.includes("客户名") || t.includes("电话");
    }, { timeout: 15000 });
    log("客户列表就绪", elapsed());
    await clearTable(page);
    log("客户表已清空", elapsed());
    await shot(page, "01_empty_list");

    // 3. 新增客户甲：含唯一名/电话/VIP(下拉)/入会(时间)/备注(多行)
    await page.getByRole("button", { name: btn("新增") }).first().click();
    const drawer = page.locator(".ant-drawer-content");
    await drawer.waitFor();
    await drawer.getByLabel("客户名").fill(uniqueName);
    await drawer.getByLabel("电话").fill("13900001111");
    await drawer.getByLabel("备注").fill("我是备注");
    await drawer.getByLabel("VIP").click();
    await pickOption(page, "是");
    await drawer.getByLabel("入会").fill("2024-01-01 10:00:00");
    await drawer.getByRole("button", { name: btn("新增") }).click();
    await page.locator(".ant-message").getByText("已新增").first().waitFor({ timeout: 15000 });
    await page.getByText("共 1 条").waitFor({ timeout: 10000 });
    await expectRowText(uniqueName);
    log("新增客户甲成功", elapsed());
    await shot(page, "02_created_a");

    // 4. 新增客户乙：验证总数 +2
    await page.getByRole("button", { name: btn("新增") }).first().click();
    const drawer2 = page.locator(".ant-drawer-content");
    await drawer2.getByLabel("客户名").fill(otherName);
    await drawer2.getByLabel("备注").fill("乙客户备注");
    await drawer2.getByRole("button", { name: btn("新增") }).click();
    await page.locator(".ant-message").getByText("已新增").first().waitFor({ timeout: 15000 });
    await page.getByText("共 2 条").waitFor({ timeout: 10000 });

    // 5. 唯一校验：再建一个同名客户 → 应被阻止（后端唯一约束对应异步校验）
    await page.getByRole("button", { name: btn("新增") }).first().click();
    const drawer3 = page.locator(".ant-drawer-content");
    await drawer3.getByLabel("客户名").fill(uniqueName);
    await drawer3.getByLabel("电话").fill("13900009999");
    await drawer3.getByRole("button", { name: btn("新增") }).click();
    await page.locator(".ant-form-item-explain-error").getByText(/已存在相同取值/).waitFor({ timeout: 15000 });
    log("唯一校验拦截生效", elapsed());
    await shot(page, "03_unique_blocked");
    await drawer3.getByRole("button", { name: btn("取消") }).click();
    await page.waitForFunction(() => !document.querySelector(".ant-drawer-open"), { timeout: 10000 });

    // 6. 全局搜索：只命中客户甲
    const searchInput = page.getByPlaceholder("全局搜索");
    await searchInput.fill(uniqueName);
    await searchInput.press("Enter");
    await page.getByText("共 1 条").waitFor({ timeout: 10000 });
    await expectRowText(uniqueName);
    await page.waitForFunction(
      (t) => !document.body.innerText.includes(t), otherName);
    log("搜索命中唯一行", elapsed());
    // 清掉搜索词，避免污染后续筛选/计数
    await searchInput.fill("");
    await searchInput.press("Enter");
    await page.getByText("共 2 条").waitFor({ timeout: 10000 });

    // 7. 列筛选：电话精确匹配 → 仅 1 行；清除筛选回到全量
    await page.waitForFunction(() => !document.querySelector(".ant-drawer-open"), { timeout: 10000 });
    await page.locator(".ant-select", { hasText: "选择筛选列" }).first().click();
    await pickOption(page, "电话");
    const filterValue = page.getByPlaceholder(/按「电话」筛选/);
    await filterValue.fill("13900001111");
    await page.getByText("共 1 条").waitFor({ timeout: 10000 });
    await page.locator(".ant-tag-close-icon").click();
    await page.getByText("共 2 条").waitFor({ timeout: 10000 });
    log("筛选/清除筛选成功", elapsed());

    // 8. 编辑客户甲：改备注
    const rowA = page.locator(".ant-table-tbody tr.ant-table-row", { hasText: uniqueName }).first();
    await rowA.getByRole("button", { name: btn("编辑") }).click();
    const editDrawer = page.locator(".ant-drawer-content");
    await editDrawer.getByLabel("备注").fill("已改备注");
    await editDrawer.getByRole("button", { name: btn("保存") }).click();
    await page.locator(".ant-message").getByText("已保存").first().waitFor({ timeout: 15000 });
    await expectRowText("已改备注");
    log("编辑保存成功", elapsed());
    await shot(page, "04_edited");

    // 9. 删除客户乙
    const rowB = page.locator(".ant-table-tbody tr.ant-table-row", { hasText: otherName }).first();
    await rowB.getByRole("button", { name: btn("删除") }).click();
    await page.locator(".ant-popconfirm").getByRole("button", { name: btn("删除") }).click();
    await page.locator(".ant-message").getByText("已删除").first().waitFor({ timeout: 15000 });
    await page.waitForFunction((t) => !document.body.innerText.includes(t), otherName);
    await page.getByText("共 1 条").waitFor({ timeout: 10000 });
    log("删除成功", elapsed());
    await shot(page, "05_deleted");

    // 10. 切到订单页 → 新增订单，外键关联下拉选客户甲
    await page.locator(".ant-menu-item", { hasText: "订单" }).click();
    // 等订单列表真正切换（thead 出现「金额」）再清残留，避免删除已卸载的客户行
    await page.waitForFunction(() => {
      const t = document.querySelector(".ant-table-thead")?.innerText || "";
      return t.includes("金额");
    }, { timeout: 15000 });
    await clearTable(page);
    await page.getByRole("button", { name: btn("新增") }).first().click();
    const orderDrawer = page.locator(".ant-drawer-content");
    await orderDrawer.getByLabel("客户").click();
    await pickOption(page, uniqueName);
    await orderDrawer.getByLabel("金额").fill("99.5");
    await orderDrawer.getByLabel("说明").fill("订单说明");
    await orderDrawer.getByRole("button", { name: btn("新增") }).click();
    await page.locator(".ant-message").getByText("已新增").first().waitFor({ timeout: 15000 });
    await page.getByText("共 1 条").waitFor({ timeout: 10000 });
    await expectRowText("订单说明");
    log("订单新增（外键下拉）成功", elapsed());
    await shot(page, "06_order_added");
  } catch (e) {
    const dump = await page.evaluate(() => {
      const inputs = Array.from(document.querySelectorAll("input")).map((i) => ({
        ph: i.placeholder, val: i.value,
      }));
      return {
        text: document.body.innerText.slice(0, 500),
        inputs,
        selects: Array.from(document.querySelectorAll(".ant-select")).map((s) => (s.innerText || "").slice(0, 30)),
      };
    }).catch(() => ({}));
    failures.push("异常: " + (e && e.message ? e.message : String(e)));
    failures.push("页面状态: " + JSON.stringify(dump));
    await shot(page, "99_error");
  } finally {
    await browser.close();
  }

  const total = Date.now() - t0;
  log("总耗时", (total / 1000).toFixed(1) + "s", `限额 ${LIMIT_MS / 1000}s`);
  if (total > LIMIT_MS) failures.push(`总耗时 ${total}ms 超过 3 分钟限额`);

  if (failures.length) {
    console.error("BROWSER_E2E_M24_FAIL");
    for (const f of failures) console.error(" -", f);
    process.exit(1);
  }
  console.log("BROWSER_E2E_M24_OK");
  process.exit(0);
})();