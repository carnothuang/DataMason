"""M3-2 真实 HTTP 点验：界面与菜单（工程室）在正式 WSGI serve 上跑恒真。

流程（真 HTTP，不经 test client）：
  启动 serve_for_e2e（waitress，8765，隔离临时工作区）→ admin 登录
  → 找到「客户与订单E2E」部署（创建人）
  → GET interfaces（menus/pages/granted/entry_path，创建人独有 DR-14）
  → 管理员成员访问 menus/interfaces → 403
  → 整组保存菜单：新建分组 + 收纳子菜单 + 追踪 id 稳定 + structure 埋点
  → 新建菜单默认不在授权集合（需授权）；授权后操作员可见
  → 自定义入口 /guestbook：lookup 命中、旧 /app/{id} 不再暴露、留空恢复
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
ROOT = Path(r"{root}")
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("DATAMASON_ADMIN_USERNAME", "admin")
os.environ.setdefault("DATAMASON_ADMIN_PASSWORD", "admin123")
os.environ.setdefault("DATAMASON_E2E_LLM_STUB", "1")
ws = Path(os.environ["DATAMASON_M32_WORKSPACE"])
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


def _labels(nodes: list[dict]) -> list[str]:
    out: list[str] = []
    for n in nodes:
        out.append(n["label"])
        out.extend(_labels(n.get("children") or []))
    return out


def main() -> int:
    env = os.environ.copy()
    env.setdefault("DATAMASON_ADMIN_USERNAME", "admin")
    env.setdefault("DATAMASON_ADMIN_PASSWORD", "admin123")
    ws = Path(tempfile.mkdtemp(prefix="datamason_m32_"))
    env["DATAMASON_M32_WORKSPACE"] = str(ws)
    wrapper = ws / "serve_m32.py"
    wrapper.write_text(WRAPPER.format(root=ROOT), encoding="utf-8")
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
            assert demo is not None, f"未找到 E2E 演示部署: {lists}"
            did = demo["id"]
            assert demo["role"] == "creator", demo
            print(f"[m32] demo deployment id={did} role=creator", flush=True)

            # --- 创建人 interfaces ---
            itf = c.get(f"/api/v1/deployments/{did}/interfaces")
            assert itf.status_code == 200, itf.text
            body = itf.json()
            assert body["menus"] and body["pages"] and body["granted_menu_ids"] == []
            assert body["entry_path"] == f"/app/{did}", body["entry_path"]
            crud_page = next(p for p in body["pages"] if p["kind"] == "crud_list")
            crud_table = crud_page["table"]
            root_menu = body["menus"][0]
            print(f"[m32] interfaces OK: pages={len(body['pages'])} menus={len(body['menus'])}",
                  flush=True)

            # --- 管理员成员：界面与菜单 403（DR-14） ---
            c.post("/api/v1/auth/users",
                   json={"username": "adminer_m32", "password": "pw", "display_name": "管理员乙"})
            c.post(f"/api/v1/deployments/{did}/members",
                   json={"username": "adminer_m32", "role": "admin"})
            with httpx.Client(base_url=BASE, timeout=30, follow_redirects=True) as c2:
                c2.post("/api/v1/auth/login", json={"username": "adminer_m32", "password": "pw"})
                assert c2.get(f"/api/v1/deployments/{did}/interfaces").status_code == 403
                assert c2.put(f"/api/v1/deployments/{did}/menus", json={"nodes": []}).status_code == 403
                assert c2.put(f"/api/v1/deployments/{did}/entry_path",
                              json={"path": "/other"}).status_code == 403
            print("[m32] 管理员成员 menus/interfaces/entry_path → 403", flush=True)

            # --- 整组保存：新建分组 + 收纳 + 追踪 id 稳定 ---
            nodes = [
                {"id": root_menu["id"], "label": root_menu["label"], "kind": "table",
                 "ref_id": root_menu["ref_id"], "parent_id": None},
                {"label": "访客专区", "kind": "table", "ref_id": None, "parent_id": None},
                {"label": "订单台账", "kind": "table", "ref_id": crud_page["id"], "parent_id": -2},
            ]
            s1 = c.put(f"/api/v1/deployments/{did}/menus", json={"nodes": nodes})
            assert s1.status_code == 200, s1.text
            saved = s1.json()["nodes"]
            assert saved[0]["id"] == root_menu["id"]
            group = saved[1]
            child = saved[2]
            assert child["parent_id"] == group["id"], saved
            print(f"[m32] 菜单布局保存 OK：分组 id={group['id']} 子菜单 id={child['id']}",
                  flush=True)

            # --- structure 埋点（menu） ---
            logs = c.get(f"/api/v1/app/{did}/api/activity-logs",
                         params={"page_size": 100}).json()["logs"]
            menu_logs = [lg for lg in logs if lg["action"] == "structure" and lg["object_kind"] == "menu"]
            assert len(menu_logs) >= 1, logs
            assert "菜单调整" in menu_logs[-1]["object_label"], menu_logs[-1]
            print("[m32] structure/object_kind=menu 埋点 OK", flush=True)

            # --- 新建菜单默认不授权 → 授权后操作员可见 ---
            c.post("/api/v1/auth/users",
                   json={"username": "op_m32", "password": "pw", "display_name": "操作员"})
            c.post(f"/api/v1/deployments/{did}/members",
                   json={"username": "op_m32", "role": "operator", "menu_ids": [root_menu["id"]]})
            with httpx.Client(base_url=BASE, timeout=30, follow_redirects=True) as c3:
                c3.post("/api/v1/auth/login", json={"username": "op_m32", "password": "pw"})
                ws_op = c3.get(f"/api/v1/app/{did}").json()
                op_labels = _labels(ws_op["menus"])
                assert op_labels == [root_menu["label"]], op_labels  # 新菜单不可见
            itf2 = c.get(f"/api/v1/deployments/{did}/interfaces").json()
            new_menu = next(m for m in itf2["menus"] if m["label"] == "订单台账")
            assert new_menu["id"] not in set(itf2["granted_menu_ids"])  # 「需授权」
            print("[m32] 新菜单默认不授权（操作员不可见）OK", flush=True)

            # 授权后可见
            members = c.get(f"/api/v1/deployments/{did}/members").json()["members"]
            op_row = next(m for m in members if m["username"] == "op_m32")
            r = c.put(f"/api/v1/deployments/{did}/members/{op_row['user_id']}",
                      json={"role": "operator",
                            "menu_ids": [root_menu["id"], new_menu["id"]]})
            assert r.status_code == 200, r.text
            with httpx.Client(base_url=BASE, timeout=30, follow_redirects=True) as c3:
                c3.post("/api/v1/auth/login", json={"username": "op_m32", "password": "pw"})
                op_labels = _labels(c3.get(f"/api/v1/app/{did}").json()["menus"])
                assert "订单台账" in op_labels, op_labels
            print("[m32] 授权后操作员可见新菜单 OK", flush=True)

            # --- 自定义入口 /guestbook ---
            assert c.get(f"/api/v1/entry/lookup?path=/app/{did}").json()["found"]
            pr = c.put(f"/api/v1/deployments/{did}/entry_path", json={"path": "/guestbook"})
            assert pr.status_code == 200, pr.text
            assert pr.json()["entry_path"] == "/guestbook"
            hit = c.get("/api/v1/entry/lookup?path=/guestbook").json()
            assert hit["found"] and hit["deployment_id"] == did
            assert c.get(f"/api/v1/entry/lookup?path=/app/{did}").json()["found"] is False
            assert c.get(f"/api/v1/app/{did}").status_code == 200  # API 命名空间仍在
            entry_log = next(lg for lg in logs + c.get(f"/api/v1/app/{did}/api/activity-logs",
                                                       params={"page_size": 100}).json()["logs"]
                             if lg["action"] == "structure" and "入口路径调整" in lg["object_label"])
            assert "guestbook" in entry_log["object_label"], entry_log
            print("[m32] 自定义入口 /guestbook 生效、旧 /app/{id} 失配、埋点 OK", flush=True)

            # 留空恢复默认
            pr2 = c.put(f"/api/v1/deployments/{did}/entry_path", json={"path": ""})
            assert pr2.json()["entry_path"] == f"/app/{did}"
            assert c.get(f"/api/v1/entry/lookup?path=/app/{did}").json()["found"]
            assert c.get("/api/v1/entry/lookup?path=/guestbook").json()["found"] is False
            print("[m32] 入口路径留空恢复 /app/{id} OK", flush=True)

            # --- 非法路径 / 保留路径 / 占用 ---
            assert c.put(f"/api/v1/deployments/{did}/entry_path",
                         json={"path": "/api/x"}).status_code == 400
            assert c.put(f"/api/v1/deployments/{did}/entry_path",
                         json={"path": "guestbook"}).status_code == 400
            print("[m32] 非法/保留路径 400 OK", flush=True)

            # --- 界面卡「调整」：改页面标题 ---
            pt = c.put(f"/api/v1/deployments/{did}/pages/{crud_page['id']}",
                       json={"title": "客户台账"})
            assert pt.status_code == 200, pt.text
            itf3 = c.get(f"/api/v1/deployments/{did}/interfaces").json()
            assert next(p for p in itf3["pages"] if p["id"] == crud_page["id"])["title"] == "客户台账"
            print("[m32] 页面标题调整 OK", flush=True)

        print("HTTP_CHECK_M32_OK", flush=True)
        return 0
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=5)
        except subprocess.TimeoutExpired:
            srv.kill()


if __name__ == "__main__":
    sys.exit(main())