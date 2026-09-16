"""M3-7 真实 HTTP 点验：销毁（创建人独有，DR-14）在正式 WSGI serve 上跑恒真。

流程（真 HTTP，不经 test client）：
  启动 serve_for_e2e（waitress，8765，隔离临时工作区）→ admin 登录
  → 建创建模式部署「M37待销毁E2E」→ 先导出（含全量日志）→ 销毁：
      清单不再显示 / 工作台 404 / 入口失配 / 独立库文件确已清 / 元数据与日志全清
  → 权限：管理员 403 / 匿名 401
  → 直连模式：复用外部库建「M37直连E2E」→ 销毁后外部库文件与连接原样保留（红线）
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

GUESTBOOK_SPEC = {
    "biz_notes": "M3-7 点验：待销毁",
    "tables": [
        {
            "name": "notes",
            "biz_name": "笔记",
            "columns": [
                {"name": "id", "biz_name": "主键", "type": "INTEGER",
                 "is_pk": True, "autoincrement": True, "nullable": False},
                {"name": "title", "biz_name": "标题", "type": "VARCHAR", "length": 80},
            ],
            "indexes": [],
        }
    ],
}

LINK_SPEC = {
    "biz_notes": "M3-7 点验：直连外部",
    "tables": [
        {
            "name": "products",
            "biz_name": "产品",
            "columns": [
                {"name": "id", "biz_name": "主键", "type": "INTEGER", "is_pk": True},
                {"name": "supplier_id", "biz_name": "供应商", "type": "INTEGER"},
                {"name": "name", "biz_name": "名称", "type": "VARCHAR", "length": 64},
                {"name": "price", "biz_name": "价格", "type": "DECIMAL"},
                {"name": "spec", "biz_name": "规格", "type": "TEXT"},
            ],
            "indexes": [],
        }
    ],
}

WRAPPER = """\
import os, sys
from pathlib import Path
ROOT = Path(__ROOT__)
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DATAMASON_ADMIN_USERNAME", "admin")
os.environ.setdefault("DATAMASON_ADMIN_PASSWORD", "admin123")
os.environ.setdefault("DATAMASON_E2E_LLM_STUB", "1")
ws = Path(os.environ["DATAMASON_M37_WORKSPACE"])
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
se._seed_external(app)
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
    ws = Path(tempfile.mkdtemp(prefix="datamason_m37_"))
    env["DATAMASON_M37_WORKSPACE"] = str(ws)
    wrapper = ws / "serve_m37.py"
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

            # ---------- 创建模式：建 → 导出 → 销毁 ----------
            created = c.post("/api/v1/deployments/confirm", json={
                "spec_json": GUESTBOOK_SPEC, "biz_name": "M37待销毁E2E", "seed_data": False,
            })
            assert created.status_code == 201, created.text
            did = created.json()["deployment_id"]
            entry_path = created.json()["entry_path"]
            c.post(f"/api/v1/app/{did}/api/tables/notes/rows", json={"title": "一条笔记"})

            pkg = c.get(f"/api/v1/deployments/{did}/export").json()
            db_name = pkg["deployment"]["db_name"]
            db_file = ws / "data" / db_name
            assert db_file.exists(), f"独立库文件应存在: {db_file}"
            assert pkg["counts"]["activity_logs"] >= 1
            print(f"[m37] 创建模式部署 id={did} 独立库={db_name}", flush=True)

            destroyed = c.post(f"/api/v1/deployments/{did}/destroy")
            assert destroyed.status_code == 200, destroyed.text
            body = destroyed.json()
            assert body["destroyed"] and body["db_file_removed"] and not body["external_preserved"]
            assert not db_file.exists(), "独立库文件应被删除"
            assert all(d["id"] != did for d in c.get("/api/v1/deployments").json())
            assert c.get(f"/api/v1/app/{did}").status_code == 404
            assert c.get(f"/api/v1/entry/lookup?path={entry_path}").json()["found"] is False
            assert c.post(f"/api/v1/deployments/{did}/destroy").status_code == 404
            print("[m37] 创建模式销毁 OK：库文件已清 / 清单移除 / 工作台 404 / 入口失配", flush=True)

            # ---------- 权限：管理员 403 / 匿名 401 ----------
            created2 = c.post("/api/v1/deployments/confirm", json={
                "spec_json": GUESTBOOK_SPEC, "biz_name": "M37权限E2E", "seed_data": False,
            }).json()
            did2 = created2["deployment_id"]
            c.post("/api/v1/auth/users",
                   json={"username": "adm_m37", "password": "pw", "display_name": "管理员乙"})
            c.post(f"/api/v1/deployments/{did2}/members",
                   json={"username": "adm_m37", "role": "admin"})
            with httpx.Client(base_url=BASE, timeout=30, follow_redirects=True) as adm:
                adm.post("/api/v1/auth/login", json={"username": "adm_m37", "password": "pw"})
                assert adm.post(f"/api/v1/deployments/{did2}/destroy").status_code == 403
            with httpx.Client(base_url=BASE, timeout=30, follow_redirects=True) as anon:
                assert anon.post(f"/api/v1/deployments/{did2}/destroy").status_code == 401
            print("[m37] 权限：管理员 403 / 匿名 401 OK", flush=True)

            # ---------- 直连模式：外部库与连接原样保留 ----------
            conns = c.get("/api/v1/deployments/link/connections").json()
            ext = next((x for x in conns if x["name"] == "E2E外部产品库"), None)
            assert ext is not None, conns
            linked = c.post("/api/v1/deployments/confirm", json={
                "spec_json": LINK_SPEC, "biz_name": "M37直连E2E", "seed_data": False,
                "mode": "link", "connection_id": ext["id"],
            })
            assert linked.status_code == 201, linked.text
            did3 = linked.json()["deployment_id"]
            ext_db = ws / "data" / "e2e_external_products.db"
            assert ext_db.exists()
            d3 = c.post(f"/api/v1/deployments/{did3}/destroy")
            assert d3.status_code == 200, d3.text
            assert d3.json()["external_preserved"] and not d3.json()["db_file_removed"]
            assert ext_db.exists(), "直连外部库文件必须保留"
            conns2 = c.get("/api/v1/deployments/link/connections").json()
            assert any(x["id"] == ext["id"] for x in conns2), "直连连接元数据必须保留"
            assert c.get(f"/api/v1/app/{did3}").status_code == 404
            print("[m37] 直连模式销毁 OK：外部库文件与连接零变化", flush=True)

        print("HTTP_CHECK_M37_OK", flush=True)
        return 0
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=5)
        except subprocess.TimeoutExpired:
            srv.kill()


if __name__ == "__main__":
    sys.exit(main())