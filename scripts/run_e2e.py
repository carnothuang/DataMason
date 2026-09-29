"""自包含真实 E2E 启动器：进程内起 werkzeug 服务 + 探活 + 跑 HTTP 全链路。

规避外部 `flask run` 后台拉起不稳/时序问题：
- 守护线程内 run_simple 起真实 HTTP 服务（127.0.0.1:8765）
- 轮询 /api/v1/health 直到就绪（最多 30s）
- 就绪后调用 scripts.e2e_real 的真实 HTTP 流程
- 主流程结束进程退出，守护线程随之结束

环境变量（必填）：
  DATAMASON_ADMIN_USERNAME / DATAMASON_ADMIN_PASSWORD
  DATAMASON_LLM_BASE_URL / DATAMASON_LLM_API_KEY / DATAMASON_LLM_MODEL
用法：
  python scripts/run_e2e.py
"""
from __future__ import annotations

import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

# 确保以仓库根为工作目录（元数据库/data 相对路径据此解析）
ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / "src"))

from werkzeug.serving import make_server  # noqa: E402

PORT = 8765
HOST = "127.0.0.1"


def wait_for_health(timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    url = f"http://{HOST}:{PORT}/api/v1/health"
    last_err = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                if resp.status == 200:
                    return True
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(0.4)
    print(f"[server] health 未在 {timeout}s 内就绪，最后错误: {last_err}")
    return False


def main() -> int:
    from datamason.app import create_app

    app = create_app()

    server = make_server(HOST, PORT, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"[server] werkzeug 已启动 http://{HOST}:{PORT}，等待 health ...")

    if not wait_for_health():
        server.shutdown()
        return 2
    print("[server] health=200，服务就绪")

    # 复用真实 HTTP E2E 流程（独立 urllib 客户端，打真实端口）
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "e2e_real", ROOT / "scripts" / "e2e_real.py"
    )
    e2e_real = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(e2e_real)

    try:
        rc = e2e_real.main()
    finally:
        server.shutdown()
    return rc


if __name__ == "__main__":
    sys.exit(main())
