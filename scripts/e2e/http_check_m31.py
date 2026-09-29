"""M3-1 真实 HTTP 点验：生命周期 v1（停止/再启动） 在正式 WSGI serve 上跑恒真。

流程（与服务端真 HTTP 交互，不经 test client）：
  启动 serve_for_e2e（waitress，8765）→ admin 登录
  → 找到「客户与订单E2E」部署（running）
  → POST stop → status=stopped；入口返回 stopped 提示页
  → 工作台 CRUD / 日志 409（数据保留）
  → 建 adminer 成员 → 其调 stop/start 403（DR-14）
  → POST start → 恢复 running；CRUD 可写
  → activity-logs 含 stop/start committed
结束杀进程。任一失败即非零退出。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent.parent
E2E_DIR = Path(__file__).resolve().parent
BASE = "http://127.0.0.1:8765"


WRAPPER = """\
import os, sys
from pathlib import Path
ROOT = Path(r"{root}")
sys.path.insert(0, str(ROOT / "src"))
import tempfile as _tf
os.environ.setdefault("DATAMASON_ADMIN_USERNAME", "admin")
os.environ.setdefault("DATAMASON_ADMIN_PASSWORD", "admin123")
os.environ.setdefault("DATAMASON_E2E_LLM_STUB", "1")
ws = Path(os.environ["DATAMASON_M31_WORKSPACE"])
from datamason.app import create_app
from datamason.config import Config
app = create_app(Config(
    meta_db_uri=f"sqlite:///{{ws/'meta.db'}}",
    data_root=str(ws / "data"),
))
import importlib.util
spec = importlib.util.spec_from_file_location("se", ROOT / "scripts" / "e2e" / "serve_for_e2e.py")
se = importlib.util.module_from_spec(spec)
spec.loader.exec_module(se)
se.seed(app)
se._run_server(app)()
"""


def wait_ready(timeout: float = 30) -> None:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        try:
            r = httpx.get(f"{BASE}/api/v1/health", timeout=2)
            if r.status_code == 200:
                return
            last = r.text
        except Exception as exc:  # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
            time.sleep(0.5)
    raise SystemExit(f"serve_for_e2e 未就绪：{last}")


def main() -> int:
    env = os.environ.copy()
    env.setdefault("DATAMASON_ADMIN_USERNAME", "admin")
    env.setdefault("DATAMASON_ADMIN_PASSWORD", "admin123")
    ws = Path(tempfile.mkdtemp(prefix="datamason_m31_"))
    env["DATAMASON_M31_WORKSPACE"] = str(ws)
    wrapper = ws / "serve_m31.py"
    wrapper.write_text(WRAPPER.format(root=ROOT), encoding="utf-8")
    srv = subprocess.Popen(
        [sys.executable, str(wrapper)],
        cwd=ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    try:
        wait_ready()
        with httpx.Client(base_url=BASE, timeout=30, follow_redirects=True) as c:
            def post(path, **kw):
                return c.post(path, **kw)

            login = post("/api/v1/auth/login",
                         json={"username": env["DATAMASON_ADMIN_USERNAME"],
                               "password": env["DATAMASON_ADMIN_PASSWORD"]})
            assert login.status_code == 200, login.text

            lists = c.get("/api/v1/deployments").json()
            demo = next((d for d in lists if d["biz_name"].startswith("客户与订单E2E")), None)
            assert demo is not None, f"未找到 E2E 演示部署: {lists}"
            did = demo["id"]
            assert demo["status"] == "running" and demo["role"] == "creator"
            print(f"[m31] demo deployment id={did} running role=creator", flush=True)

            # --- 停止 ---
            r = post(f"/api/v1/deployments/{did}/stop")
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "stopped", r.text
            print("[m31] stop → 200 stopped", flush=True)

            # 入口=停止态提示页
            w = c.get(f"/api/v1/app/{did}")
            assert w.status_code == 200 and w.json()["status"] == "stopped", w.text
            assert w.json()["menus"] == [] and w.json()["pages"] == [], w.text
            print("[m31] 入口返回停止态提示页", flush=True)

            # 工作台 CRUD / 日志 409
            api = f"/api/v1/app/{did}/api"
            assert c.get(f"{api}/tables/customers/rows").status_code == 409
            assert post(f"{api}/tables/customers/rows",
                        json={"name": "拦截测试"}).status_code == 409
            assert c.get(f"{api}/activity-logs").status_code == 409
            print("[m31] 停止态工作台 CRUD/日志 409", flush=True)

            # --- 成员 stop → 403（DR-14） ---
            post("/api/v1/auth/users",
                 json={"username": "adminer_m31", "password": "pw",
                       "display_name": "管理员乙"})
            post(f"/api/v1/deployments/{did}/members",
                 json={"username": "adminer_m31", "role": "admin"})
            with httpx.Client(base_url=BASE, timeout=30,
                              follow_redirects=True) as c2:
                c2.post("/api/v1/auth/login",
                        json={"username": "adminer_m31", "password": "pw"})
                assert c2.post(f"/api/v1/deployments/{did}/start").status_code == 403
                assert c2.post(f"/api/v1/deployments/{did}/stop").status_code == 403
            print("[m31] 管理员成员 stop/start → 403（创建人独有 DR-14）", flush=True)

            # --- 再启动 ---
            r = post(f"/api/v1/deployments/{did}/start")
            assert r.status_code == 200 and r.json()["status"] == "running", r.text
            w = c.get(f"/api/v1/app/{did}")
            assert w.status_code == 200 and w.json()["status"] == "running", w.text
            created = post(f"{api}/tables/customers/rows",
                           json={"name": "恢复后可写"})
            assert created.status_code == 201, created.text
            print("[m31] 再启动 → running；CRUD 可写 201", flush=True)

            # --- 日志含 stop/start committed ---
            logs = c.get(f"{api}/activity-logs", params={"page_size": 100}).json()["logs"]
            actions = {lg["action"] for lg in logs}
            assert {"stop", "start"} <= actions, actions
            stop_log = next(lg for lg in logs if lg["action"] == "stop")
            assert "已停止" in stop_log["object_label"], stop_log
            print("[m31] activity-logs 含 stop/start committed OK", flush=True)

        print("HTTP_CHECK_M31_OK", flush=True)
        return 0
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=5)
        except subprocess.TimeoutExpired:
            srv.kill()


if __name__ == "__main__":
    sys.exit(main())