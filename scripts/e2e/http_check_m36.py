"""M3-6 真实 HTTP 点验：配置包导出（DR-03/§五）在正式 WSGI serve 上跑恒真。

流程（真 HTTP，不经 test client）：
  启动 serve_for_e2e（waitress，8765，隔离临时工作区）→ admin 登录 → 找到「客户与订单E2E」
  → 创建人导出：JSON 附件（Content-Disposition）、含 deployment/table_specs/page_specs/
    deployment_menus/crud_configs/flow_specs/activity_logs，且 json.loads 可离线打开
  → 造一条业务数据 → 再导出含该 create 日志（committed）
  → 管理员 200 / 操作员 403 / 匿名 401
  → 停止后仍可导出（status=stopped）
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
BASE = "http://127.0.0.1:8765"

WRAPPER = """\
import os, sys
from pathlib import Path
ROOT = Path(__ROOT__)
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DATAMASON_ADMIN_USERNAME", "admin")
os.environ.setdefault("DATAMASON_ADMIN_PASSWORD", "admin123")
os.environ.setdefault("DATAMASON_E2E_LLM_STUB", "1")
ws = Path(os.environ["DATAMASON_M36_WORKSPACE"])
from server.app import create_app
from server.config import Config
app = create_app(Config(
    meta_db_uri=f"sqlite:///{ws/'meta.db'}",
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
    ws = Path(tempfile.mkdtemp(prefix="datamason_m36_"))
    env["DATAMASON_M36_WORKSPACE"] = str(ws)
    wrapper = ws / "serve_m36.py"
    wrapper.write_text(WRAPPER.replace("__ROOT__", repr(str(ROOT))), encoding="utf-8")
    srv = subprocess.Popen(
        [sys.executable, str(wrapper)],
        cwd=ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    try:
        wait_ready()
        with httpx.Client(base_url=BASE, timeout=30, follow_redirects=True) as c:
            assert c.post("/api/v1/auth/login",
                          json={"username": env["DATAMASON_ADMIN_USERNAME"],
                                "password": env["DATAMASON_ADMIN_PASSWORD"]}).status_code == 200
            lists = c.get("/api/v1/deployments").json()
            demo = next((d for d in lists if d["biz_name"].startswith("客户与订单E2E")), None)
            assert demo is not None, f"未找到 E2E 演示部署: {lists}"
            did = demo["id"]
            assert demo["role"] == "creator", demo
            print(f"[m36] demo deployment id={did} role=creator", flush=True)

            # --- 创建人导出：JSON 附件 + 结构完整 + 可离线解析 ---
            resp = c.get(f"/api/v1/deployments/{did}/export")
            assert resp.status_code == 200, resp.text
            assert "attachment" in resp.headers.get("Content-Disposition", ""), resp.headers
            pkg = json.loads(resp.text)  # 可离线打开
            assert pkg["format"] == "datamason.deployment.export" and pkg["version"] == 1
            d = pkg["deployment"]
            assert d["id"] == did and d["connection"]["dialect"] == "sqlite"
            assert "conn_uri" not in d["connection"]  # 脱敏
            for key in ("table_specs", "page_specs", "deployment_menus",
                        "crud_configs", "flow_specs", "activity_logs", "counts"):
                assert key in pkg, key
            assert pkg["counts"]["table_specs"] == len(pkg["table_specs"]) == 2
            assert pkg["counts"]["activity_logs"] == len(pkg["activity_logs"]) >= 1
            assert all(lg["status"] == "committed" for lg in pkg["activity_logs"])
            print(f"[m36] 创建人导出 OK：tables={pkg['counts']['table_specs']} "
                  f"pages={pkg['counts']['page_specs']} menus={pkg['counts']['deployment_menus']} "
                  f"logs={pkg['counts']['activity_logs']}", flush=True)

            # --- 造一条业务数据 → 导出含该 create 日志 ---
            created = c.post(f"/api/v1/app/{did}/api/tables/customers/rows",
                             json={"name": "导出点验客户", "phone": "13900000000"})
            assert created.status_code == 201, created.text
            pkg2 = c.get(f"/api/v1/deployments/{did}/export").json()
            assert any(lg["action"] == "create" and lg["object_table"] == "customers"
                       for lg in pkg2["activity_logs"]), "缺业务 create 日志"
            assert any(lg["actor_display"] == "管理员" for lg in pkg2["activity_logs"])
            print("[m36] 全量 committed 日志（含业务 create）OK", flush=True)

            # --- 管理员 200 / 操作员 403 / 匿名 401 ---
            c.post("/api/v1/auth/users",
                   json={"username": "adm_m36", "password": "pw", "display_name": "管理员乙"})
            c.post(f"/api/v1/deployments/{did}/members",
                   json={"username": "adm_m36", "role": "admin"})
            c.post("/api/v1/auth/users",
                   json={"username": "op_m36", "password": "pw", "display_name": "操作员丙"})
            c.post(f"/api/v1/deployments/{did}/members",
                   json={"username": "op_m36", "role": "operator"})
            with httpx.Client(base_url=BASE, timeout=30, follow_redirects=True) as adm:
                adm.post("/api/v1/auth/login", json={"username": "adm_m36", "password": "pw"})
                assert adm.get(f"/api/v1/deployments/{did}/export").status_code == 200
            with httpx.Client(base_url=BASE, timeout=30, follow_redirects=True) as op:
                op.post("/api/v1/auth/login", json={"username": "op_m36", "password": "pw"})
                assert op.get(f"/api/v1/deployments/{did}/export").status_code == 403
            with httpx.Client(base_url=BASE, timeout=30, follow_redirects=True) as anon:
                assert anon.get(f"/api/v1/deployments/{did}/export").status_code == 401
            print("[m36] 权限：管理员 200 / 操作员 403 / 匿名 401 OK", flush=True)

            # --- 停止态也可导出 ---
            assert c.post(f"/api/v1/deployments/{did}/stop").status_code == 200
            stopped = c.get(f"/api/v1/deployments/{did}/export")
            assert stopped.status_code == 200, stopped.text
            assert stopped.json()["deployment"]["status"] == "stopped"
            print("[m36] 停止态导出 OK", flush=True)

        print("HTTP_CHECK_M36_OK", flush=True)
        return 0
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=5)
        except subprocess.TimeoutExpired:
            srv.kill()


if __name__ == "__main__":
    sys.exit(main())