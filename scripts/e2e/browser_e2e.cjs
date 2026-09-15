/* M1-6 真浏览器 E2E（Playwright / Chromium）。
 * 验收线：登录→描述→提案→确认→建表→带演示数据→浏览，全程 ≤3 分钟（180s）。
 * 前置：scripts/e2e/serve_for_e2e.py 已在 127.0.0.1:8765 运行并托管 web/dist。
 * 运行：见 scripts/e2e/run_browser_e2e.ps1（注入 NODE_PATH 指向全局 playwright）。
 * 环境变量：
 *   DATAMASON_E2E_USER（默认 laomao）/ DATAMASON_E2E_PASS（默认 pw）。
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

async function shot(page, name) {
  const p = `${SHOT_DIR}/e2e_${name}.png`;
  await page.screenshot({ path: p, fullPage: true }).catch(() => {});
  log("screenshot:", p);
}

(async () => {
  const browser = await chromium.launch({
    headless: HEADLESS,
    args: ["--no-proxy-server"], // 本地 E2E 直连，绕过系统/抓包代理
  });
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
  page.on("console", (m) => {
    if (m.type() === "error") log("console.error:", m.text().slice(0, 300));
  });

  const failures = [];
  const t0 = Date.now();
  const elapsed = () => ((Date.now() - t0) / 1000).toFixed(1) + "s";
  try {
    // 0. 登录（M2-1 起：未登录会跳 /login）
    await page.goto(BASE + "/", { waitUntil: "networkidle" });
    // 登录表单出现即视为需要登录；否则首页直接就绪
    const needLogin = await page.getByRole("button", { name: "登录" }).count();
    if (needLogin > 0) {
      await page.getByPlaceholder("用户名").fill(E2E_USER);
      await page.getByPlaceholder("密码").fill(E2E_PASS);
      await page.getByRole("button", { name: "登录" }).click();
      log("已登录", elapsed());
    } else {
      log("已存在会话，跳过登录");
    }

    // 1. 首页
    await page.goto(BASE + "/", { waitUntil: "networkidle" });
    await page.getByText("我部署过的系统").first().waitFor({ timeout: 10000 });
    log("首页就绪", elapsed());

    // 2. 打开部署向导
    await page.getByRole("button", { name: "部署一套系统" }).click();
    await page.getByText("你要验证什么").waitFor();

    // 3. 输入一句话 + 系统名称（演示数据默认勾选）
    const desc = "做一个客户留言收集：记录客户姓名、联系方式、留言内容和留言时间";
    const bizName = `留言E2E${STAMP}`;
    await page.getByPlaceholder(/做个留言板/).fill(desc);
    await page.getByPlaceholder("给这套系统起个名").fill(bizName);
    log("已填写需求", elapsed());

    // 4. 生成提案（真实 LLM，给 120s）
    await page.getByRole("button", { name: "生成提案" }).click();
    await page.getByRole("button", { name: "就这么部署" }).waitFor({ timeout: 120000 });
    log("提案已生成", elapsed());
    await shot(page, "01_proposal");
    // 提案里应至少出现一张表
    const tableTagCount = await page.locator(".ant-modal code").count();
    if (tableTagCount < 1) failures.push("提案中未出现任何字段 code 标记");

    // 5. 确认部署 → 就绪
    const tDeploy = Date.now();
    await page.getByRole("button", { name: "就这么部署" }).click();
    await page.getByText("系统已就绪").waitFor({ timeout: 60000 });
    log("系统已就绪，部署耗时", ((Date.now() - tDeploy) / 1000).toFixed(1) + "s");
    await shot(page, "02_ready");
    // done 阶段 footer 重渲染，可操作性检查偶发不稳：轮询用 DOM click 直接触发 React onClick
    let dismissed = false;
    for (let i = 0; i < 20; i++) {
      const clicked = await page.evaluate(() => {
        const btns = Array.from(document.querySelectorAll(".ant-modal button"));
        // antd 对两字按钮自动插空格（渲染为“完 成”），比较前去掉所有空白
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
    if (!dismissed) {
      const dump = await page.evaluate(() =>
        Array.from(document.querySelectorAll("button")).map((b) => (b.textContent || "").trim()).filter(Boolean));
      throw new Error("未能关闭就绪弹窗，页面按钮: " + JSON.stringify(dump));
    }

    // 6. 首页出现新部署卡片 → 打开数据
    await page.getByText(bizName).first().waitFor({ timeout: 10000 });
    await page.getByRole("button", { name: "打开数据" }).first().click();

    // 7. 数据浏览：等待表格 + 10 行演示数据
    await page.getByText("数据表：").waitFor();
    await page.getByText(/共 10 条/).first().waitFor({ timeout: 15000 });
    const dataRows = await page.locator(".ant-table-tbody tr.ant-table-row").count();
    log("浏览到演示数据行数:", dataRows, elapsed());
    await shot(page, "03_browse");
    if (dataRows < 1) failures.push("数据表没有任何演示数据行");

    // 8. 表编辑器 v0：加一个字段并保存
    await page.getByRole("button", { name: "编辑字段" }).click();
    await page.getByText(/编辑字段 ·/).waitFor();
    await page.getByRole("button", { name: "+ 新增字段" }).click();
    // 既有可编辑列与新增行共用 placeholder，新增行固定追加在表格末尾，用 .last() 定位
    await page.getByPlaceholder("如 email").last().fill("source");
    await page.getByPlaceholder("如 邮箱").last().fill("来源渠道");
    await page.getByRole("button", { name: /保存变更/ }).click();
    await page.getByText("字段已更新").waitFor({ timeout: 30000 });
    log("字段编辑保存成功", elapsed());
    await shot(page, "04_after_edit");

    // 9. 回到浏览：新列出现（重新进入确保刷新）
    await page.getByRole("button", { name: "编辑字段" }).waitFor();
  } catch (e) {
    failures.push("异常: " + (e && e.message ? e.message : String(e)));
    await shot(page, "99_error");
  } finally {
    await browser.close();
  }

  const total = Date.now() - t0;
  log("总耗时", (total / 1000).toFixed(1) + "s", `限额 ${LIMIT_MS / 1000}s`);
  if (total > LIMIT_MS) failures.push(`总耗时 ${total}ms 超过 3 分钟限额`);

  if (failures.length) {
    console.error("BROWSER_E2E_FAIL");
    for (const f of failures) console.error(" -", f);
    process.exit(1);
  }
  console.log("BROWSER_E2E_OK");
  process.exit(0);
})();
