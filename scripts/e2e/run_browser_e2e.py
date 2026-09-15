"""M1-6 编排：拉起服务 → health 探活 → 全局 node 跑 Playwright 真浏览器 E2E → 回收。

- 服务：sys.executable scripts/e2e/serve_for_e2e.py（继承当前环境变量里的 LLM/管理员凭证）
- 浏览器：本机全局 Node 的 playwright（NODE_PATH 指向全局 node_modules），不向 venv 装包
- 用法（PowerShell，先设 DATAMASON_LLM_* / DATAMASON_ADMIN_*）：
    python scripts/e2e/run_browser_e2e.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
os.chdir(ROOT)

HOST, PORT = "127.0.0.1", 8765
HEALTH = f"http://{HOST}:{PORT}/api/v1/health"

# 本机全局 Node（含全局 playwright 与已下载的 chromium）
GLOBAL_NODE = r"C:\nvm4w\nodejs\node.exe"
GLOBAL_NODE_MODULES = r"C:\nvm4w\nodejs\node_modules"
SPEC = str(ROOT / "scripts" / "e2e" / "browser_e2e.cjs")


def wait_health(proc, timeout: float = 40.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(HEALTH, timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.4)
    return False


def main() -> int:
    server = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "e2e" / "serve_for_e2e.py")],
        cwd=str(ROOT), env=dict(os.environ),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace",
    )

    server_log = ROOT / ".workbuddy" / "tmp" / "m16_server.log"
    server_log.parent.mkdir(parents=True, exist_ok=True)

    node_env = dict(os.environ)
    node_env["NODE_PATH"] = GLOBAL_NODE_MODULES
    node_env["E2E_SHOT_DIR"] = str(ROOT / ".workbuddy" / "tmp")

    rc = 1
    try:
        if not wait_health(server):
            print("服务未就绪")
            return 2
        print("[orchestrator] 服务就绪，启动真浏览器 E2E")
        run = subprocess.run(
            [GLOBAL_NODE, SPEC], cwd=str(ROOT), env=node_env,
        )
        rc = run.returncode
    finally:
        server.terminate()
        try:
            out, _ = server.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            out, _ = server.communicate()
        server_log.write_text(out or "", encoding="utf-8")
        print(f"[orchestrator] 服务日志已保存 {server_log}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
