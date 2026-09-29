"""M4-1 真实 HTTP 点验：问数据后端（NL → 只读 SELECT → 行集 + 一句话结论）在正式 WSGI serve 上跑通。

流程（真 HTTP，不经 test client）：
  启动 serve_for_e2e（waitress，8765，隔离临时工作区 + 确定性 LLM 桩）
  → admin 登录 → 预置演示部署「客户与订单E2E」→ 经 CRUD 写入一条客户
  → POST /app/{id}/qa/ask：返回结论 + 行集 + SQL，行集含写入的客户
  → 操作员成员问数据 → 403（默认不开放）
结束杀进程。任一失败即非零退出。
"""
from __future__ import annotations

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
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("DATAMASON_ADMIN_USERNAME", "admin")
os.environ.setdefault("DATAMASON_ADMIN_PASSWORD", "admin123")
ws = Path(os.environ["DATAMASON_M41_WORKSPACE"])
from datamason.app import create_app
from datamason.config import Config
app = create_app(Config(
    meta_db_uri=f"sqlite:///{ws/'meta.db'}",
    data_root=str(ws / "data"),
))
import importlib.util
spec = importlib.util.spec_from_file_location("se", ROOT / "scripts" / "e2e" / "serve_for_e2e.py")
se = importlib.util.module_from_spec(spec)
spec.loader.exec_module(se)
se.seed(app)
se._install_stub_llm(app)
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
    ws = Path(tempfile.mkdtemp(prefix="datamason_m41_"))
    env["DATAMASON_M41_WORKSPACE"] = str(ws)
    wrapper = ws / "serve_m41.py"
    wrapper.write_text(WRAPPER.replace("__ROOT__", repr(str(ROOT))), encoding="utf-8")
    srv = subprocess.Popen(
        [sys.executable, str(wrapper)],
        cwd=ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    try:
        wait_ready()
        with httpx.Client(base_url=BASE, timeout=30, follow_redirects=True) as c:
            login = c.post("/api/v1/auth/login",
                           json={"username": env["DATAMASON_ADMIN_USERNAME"],
                                 "password": env["DATAMASON_ADMIN_PASSWORD"]})
            assert login.status_code == 200, login.text

            lists = c.get("/api/v1/deployments").json()
            demo = next((d for d in lists if d["biz_name"].startswith("客户与订单E2E")), None)
            assert demo is not None, f"未找到演示部署: {lists}"
            did = demo["id"]
            print(f"[m41] demo deployment id={did} role={demo['role']}", flush=True)

            # 经 CRUD 写一条客户，保证问数据有真实行可查
            created = c.post(f"/api/v1/app/{did}/api/tables/customers/rows",
                             json={"name": "问数据客户"})
            assert created.status_code == 201, created.text
            print("[m41] CRUD 写入客户 OK", flush=True)

            # 问数据：只读查询 → 结论 + 行集 + SQL
            resp = c.post(f"/api/v1/app/{did}/qa/ask", json={"question": "有哪些客户？"})
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["row_count"] >= 1, body
            assert "customers" in body["sql"].lower(), body
            assert body["conclusion"], body
            assert "id" in body["columns"] and "name" in body["columns"], body["columns"]
            assert any(r.get("name") == "问数据客户" for r in body["rows"]), body["rows"]
            print(f"[m41] 问数据 OK：{body['conclusion']}（{body['row_count']} 行）", flush=True)

            # 操作员默认不开放问数据（403）
            c.post("/api/v1/auth/users",
                   json={"username": "op_m41", "password": "pw", "display_name": "操作员"})
            c.post(f"/api/v1/deployments/{did}/members",
                   json={"username": "op_m41", "role": "operator"})
            with httpx.Client(base_url=BASE, timeout=30, follow_redirects=True) as c2:
                c2.post("/api/v1/auth/login", json={"username": "op_m41", "password": "pw"})
                op = c2.post(f"/api/v1/app/{did}/qa/ask", json={"question": "有哪些客户？"})
                assert op.status_code == 403, op.text
            print("[m41] 操作员问数据 403 OK", flush=True)

        print("HTTP_CHECK_M41_OK", flush=True)
        return 0
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=5)
        except subprocess.TimeoutExpired:
            srv.kill()


if __name__ == "__main__":
    sys.exit(main())
