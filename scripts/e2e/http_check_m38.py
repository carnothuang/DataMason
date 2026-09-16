"""M3-8 真实 HTTP 点验：API 开放（DR-11）在正式 WSGI serve 上跑恒真。

流程（真 HTTP，不经 test client）：
  启动 serve_for_e2e（waitress，8765，隔离临时工作区）→ admin 登录 → 找到「客户与订单E2E」
  → 创建人发放密钥（明文仅回显一次）→ 列表掩码回读
  → 匿名以 X-API-Key 读/写该部署 CRUD（列白名单/归属记系统 0），写操作进日志且 actor 标记 key 名
  → Bearer 亦可用；非 CRUD 接口 403；管理员管理密钥 403
  → 吊销后立即 401
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
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DATAMASON_ADMIN_USERNAME", "admin")
os.environ.setdefault("DATAMASON_ADMIN_PASSWORD", "admin123")
os.environ.setdefault("DATAMASON_E2E_LLM_STUB", "1")
ws = Path(os.environ["DATAMASON_M38_WORKSPACE"])
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
    ws = Path(tempfile.mkdtemp(prefix="datamason_m38_"))
    env["DATAMASON_M38_WORKSPACE"] = str(ws)
    wrapper = ws / "serve_m38.py"
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
            assert demo is not None, lists
            did = demo["id"]
            print(f"[m38] demo deployment id={did}", flush=True)

            # --- 发放密钥 + 掩码回读 ---
            created = c.post(f"/api/v1/deployments/{did}/api-keys", json={"name": "订单同步"})
            assert created.status_code == 201, created.text
            raw = created.json()["key"]
            assert raw.startswith("dm_") and len(raw) > 20
            listing = c.get(f"/api/v1/deployments/{did}/api-keys").json()
            assert listing["keys"][0]["masked"] == f"{raw[:10]}…" and raw not in str(listing)
            print(f"[m38] 发放密钥 OK：{listing['keys'][0]['masked']}（明文不回显）", flush=True)

            # --- key 认证：读真实数据 ---
            with httpx.Client(base_url=BASE, timeout=30, follow_redirects=True) as api:
                got = api.get(f"/api/v1/app/{did}/api/tables/customers/rows",
                              headers={"X-API-Key": raw})
                assert got.status_code == 200, got.text
                assert "data" in got.json() and "total" in got.json()
                # 无效 key → 401；非 CRUD 接口 → 403
                assert api.get(f"/api/v1/app/{did}/api/tables/customers/rows",
                               headers={"X-API-Key": "dm_bogus"}).status_code == 401
                assert api.get("/api/v1/deployments",
                               headers={"X-API-Key": raw}).status_code == 403
                # 写：列白名单 + 归属列记系统(0) + 日志 actor=key 名
                w = api.post(f"/api/v1/app/{did}/api/tables/customers/rows",
                             json={"name": "外部同步客户", "phone": "13800000000"},
                             headers={"X-API-Key": raw})
                assert w.status_code == 201, w.text
                pk = w.json()["id"]
                # Bearer 亦可
                row = api.get(f"/api/v1/app/{did}/api/tables/customers/rows/{pk}",
                              headers={"Authorization": f"Bearer {raw}"})
                assert row.status_code == 200 and row.json()["created_by"] == 0, row.text
            print("[m38] key 读写 + 归属记系统(0) + Bearer OK", flush=True)

            logs = c.get(f"/api/v1/app/{did}/api/activity-logs",
                         params={"page_size": 100}).json()["logs"]
            entry = next(lg for lg in logs
                         if lg["action"] == "create" and lg["object_table"] == "customers"
                         and lg["object_pk"] == str(pk))
            assert entry["actor_user_id"] is None
            assert entry["actor_display"] == "API key「订单同步」", entry
            print("[m38] key 写操作进日志且 actor 标记 key 名 OK", flush=True)

            # --- 权限：管理员管理密钥 403 ---
            c.post("/api/v1/auth/users",
                   json={"username": "adm_m38", "password": "pw", "display_name": "管理员乙"})
            c.post(f"/api/v1/deployments/{did}/members",
                   json={"username": "adm_m38", "role": "admin"})
            with httpx.Client(base_url=BASE, timeout=30, follow_redirects=True) as adm:
                adm.post("/api/v1/auth/login", json={"username": "adm_m38", "password": "pw"})
                assert adm.get(f"/api/v1/deployments/{did}/api-keys").status_code == 403
                assert adm.post(f"/api/v1/deployments/{did}/api-keys",
                                json={"name": "x"}).status_code == 403
            print("[m38] 密钥管理=创建人独有（管理员 403）OK", flush=True)

            # --- 吊销 → 立即 401 ---
            key_id = listing["keys"][0]["id"]
            assert c.delete(f"/api/v1/deployments/{did}/api-keys/{key_id}").status_code == 200
            with httpx.Client(base_url=BASE, timeout=30, follow_redirects=True) as api:
                assert api.get(f"/api/v1/app/{did}/api/tables/customers/rows",
                               headers={"X-API-Key": raw}).status_code == 401
            print("[m38] 吊销后立即 401 OK", flush=True)

        print("HTTP_CHECK_M38_OK", flush=True)
        return 0
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=5)
        except subprocess.TimeoutExpired:
            srv.kill()


if __name__ == "__main__":
    sys.exit(main())