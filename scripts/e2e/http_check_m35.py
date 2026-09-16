"""M3-5 真实 HTTP 点验：访客公开页 + 分享链接绑定身份（DR-04）在正式 WSGI serve 上跑恒真。

流程（真 HTTP，不经 test client）：
  启动 serve_for_e2e（waitress，8765，隔离临时工作区）→ 预置含「留言」表的「M35留言板E2E」部署
  → 匿名：GET 公开表单元数据 200 → 提交 201 → GET 工作台/数据 401（看不到菜单与数据）
  → 创建人：可见访客行（created_by=系统/0）+ activity_logs（action=create, actor=NULL 显示「系统」）
  → 分享链接：管理员生成 403（创建人独有）；创建人生成 → 操作员账号 accept 后按档位见授权菜单
  → 无效/已撤销 token 404；管理员链接 → 接受者成管理员见全部菜单
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
    "biz_notes": "M3-5 点验：访客留言板",
    "tables": [
        {
            "name": "messages",
            "biz_name": "留言",
            "columns": [
                {"name": "id", "biz_name": "主键", "type": "INTEGER",
                 "is_pk": True, "autoincrement": True, "nullable": False},
                {"name": "visitor", "biz_name": "访客", "type": "VARCHAR",
                 "length": 64, "nullable": False},
                {"name": "content", "biz_name": "内容", "type": "TEXT"},
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
ws = Path(os.environ["DATAMASON_M35_WORKSPACE"])
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
from server.deploy import service as ds
with app.app_context():
    ds.confirm(app, __SPEC__, biz_name="M35留言板E2E", seed_data=False)
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
    ws = Path(tempfile.mkdtemp(prefix="datamason_m35_"))
    env["DATAMASON_M35_WORKSPACE"] = str(ws)
    wrapper = ws / "serve_m35.py"
    wrapper.write_text(
        WRAPPER.replace("__ROOT__", repr(str(ROOT))).replace("__SPEC__", repr(GUESTBOOK_SPEC)),
        encoding="utf-8",
    )
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
            demo = next((d for d in lists if d["biz_name"].startswith("M35留言板E2E")), None)
            assert demo is not None, f"未找到 M35 演示部署: {lists}"
            did = demo["id"]
            assert demo["role"] == "creator", demo
            itf = c.get(f"/api/v1/deployments/{did}/interfaces").json()
            public_page = next(p for p in itf["pages"] if p["kind"] == "public_form")
            crud_page = next(p for p in itf["pages"] if p["kind"] == "crud_list")
            menu = itf["menus"][0]
            print(f"[m35] demo deployment id={did} public_page={public_page['id']}", flush=True)

            # --- 匿名：读公开表单 200 / 提交 201 / 工作台与数据 401 ---
            with httpx.Client(base_url=BASE, timeout=30, follow_redirects=True) as anon:
                meta = anon.get(f"/api/v1/public/{did}/pages/{public_page['id']}")
                assert meta.status_code == 200, meta.text
                body = meta.json()
                assert body["kind"] == "public_form" and body["biz_name"]
                assert {f["name"] for f in body["fields"]} == {"visitor", "content"}
                sub = anon.post(f"/api/v1/public/{did}/pages/{public_page['id']}/submit",
                                json={"visitor": "匿名访客", "content": "你好，瓦匠"})
                assert sub.status_code == 201, sub.text
                assert anon.get(f"/api/v1/app/{did}").status_code == 401
                assert anon.get(f"/api/v1/app/{did}/api/tables/messages/rows").status_code == 401
                print("[m35] 匿名：公开表单可读/可提交，菜单与数据不可见 OK", flush=True)

            # --- 创建人：访客行归属=系统 + activity_logs(actor=NULL) ---
            rows = c.get(f"/api/v1/app/{did}/api/tables/messages/rows").json()["data"]
            row = next(r for r in rows if r["visitor"] == "匿名访客")
            assert row["created_by"] == 0, row
            logs = c.get(f"/api/v1/app/{did}/api/activity-logs",
                         params={"page_size": 100}).json()["logs"]
            entry = next(lg for lg in logs
                         if lg["action"] == "create" and lg["object_table"] == "messages"
                         and lg["actor_user_id"] is None)
            assert entry["actor_display"] == "系统", entry
            print("[m35] 访客提交入库（归属系统）+ activity_logs(系统) OK", flush=True)

            # --- 分享链接：管理员 403；创建人生成操作员链接 ---
            c.post("/api/v1/auth/users",
                   json={"username": "adm_m35", "password": "pw", "display_name": "管理员乙"})
            c.post(f"/api/v1/deployments/{did}/members",
                   json={"username": "adm_m35", "role": "admin"})
            with httpx.Client(base_url=BASE, timeout=30, follow_redirects=True) as adm:
                adm.post("/api/v1/auth/login", json={"username": "adm_m35", "password": "pw"})
                assert adm.post(f"/api/v1/deployments/{did}/share-links",
                                json={"role": "operator", "menu_ids": [menu["id"]]}).status_code == 403
            resp = c.post(f"/api/v1/deployments/{did}/share-links",
                          json={"role": "operator", "menu_ids": [menu["id"]]})
            assert resp.status_code == 201, resp.text
            token = resp.json()["token"]
            print("[m35] 分享=创建人独有；操作员链接已生成 OK", flush=True)

            # --- 新用户接受链接 → 绑定操作员，只见授权菜单 ---
            c.post("/api/v1/auth/users",
                   json={"username": "op_m35", "password": "pw", "display_name": "操作员丙"})
            with httpx.Client(base_url=BASE, timeout=30, follow_redirects=True) as op:
                op.post("/api/v1/auth/login", json={"username": "op_m35", "password": "pw"})
                acc = op.post(f"/api/v1/share/{token}/accept")
                assert acc.status_code == 200, acc.text
                assert acc.json()["role"] == "operator"
                ws = op.get(f"/api/v1/app/{did}").json()
                assert [m["label"] for m in ws["menus"]] == [menu["label"]], ws["menus"]
                print("[m35] 接受链接绑定为操作员，只见授权菜单 OK", flush=True)

            # --- 管理员链接 → 接受者见全部菜单；无效/撤销 404 ---
            admin_token = c.post(f"/api/v1/deployments/{did}/share-links",
                                 json={"role": "admin"}).json()["token"]
            c.post("/api/v1/auth/users",
                   json={"username": "wang_m35", "password": "pw", "display_name": "管理员丁"})
            with httpx.Client(base_url=BASE, timeout=30, follow_redirects=True) as wang:
                wang.post("/api/v1/auth/login", json={"username": "wang_m35", "password": "pw"})
                acc2 = wang.post(f"/api/v1/share/{admin_token}/accept")
                assert acc2.status_code == 200 and acc2.json()["role"] == "admin", acc2.text
                assert wang.get(f"/api/v1/app/{did}").json()["role"] == "admin"
                assert wang.post("/api/v1/share/nope-token/accept").status_code == 404
            listing = c.get(f"/api/v1/deployments/{did}/share-links").json()
            op_link = next(l for l in listing["links"] if l["token"] == token)
            assert c.delete(f"/api/v1/deployments/{did}/share-links/{op_link['id']}").status_code == 200
            with httpx.Client(base_url=BASE, timeout=30, follow_redirects=True) as op2:
                op2.post("/api/v1/auth/login", json={"username": "op_m35", "password": "pw"})
                assert op2.post(f"/api/v1/share/{token}/accept").status_code == 404
            print("[m35] 管理员链接绑定 / 撤销后 404 OK", flush=True)

        print("HTTP_CHECK_M35_OK", flush=True)
        return 0
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=5)
        except subprocess.TimeoutExpired:
            srv.kill()


if __name__ == "__main__":
    sys.exit(main())