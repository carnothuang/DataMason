"""M3-4 真实 HTTP 点验：流程向导 v0（DR-06）在正式 WSGI serve 上跑恒真。

流程（真 HTTP，不经 test client）：
  启动 serve_for_e2e（waitress，8765，隔离临时工作区）→ 预置含状态列的「M34流程E2E」部署
  → admin 登录 → 创建 3 步流程（收货登记/质检/入库确认，绑 status 字段）
  → 流程页生成（kind=flow，携带 flow_id）→ 挂菜单 kind=flow → 授权操作员可见
  → 运行态：当前步条件未满足 → 推进 409；录入记录 → 条件满足
  → 逐步推进：状态字段随步推进（待质检→已质检→已入库），每步写 activity_logs(action=flow)
  → 完成后推进 409 → 删除流程连带清理菜单/流程页
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

FLOW_SPEC = {
    "biz_notes": "M3-4 流程向导点验：入库流程（含状态列）",
    "tables": [
        {
            "name": "goods",
            "biz_name": "入库单",
            "columns": [
                {"name": "id", "biz_name": "主键", "type": "INTEGER",
                 "is_pk": True, "autoincrement": True, "nullable": False},
                {"name": "name", "biz_name": "货物", "type": "VARCHAR",
                 "length": 64, "nullable": False},
                {"name": "status", "biz_name": "进度", "type": "VARCHAR", "length": 16},
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
ws = Path(os.environ["DATAMASON_M34_WORKSPACE"])
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
    ds.confirm(app, __SPEC__, biz_name="M34流程E2E", seed_data=False)
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
    ws = Path(tempfile.mkdtemp(prefix="datamason_m34_"))
    env["DATAMASON_M34_WORKSPACE"] = str(ws)
    wrapper = ws / "serve_m34.py"
    wrapper.write_text(
        WRAPPER.replace("__ROOT__", repr(str(ROOT))).replace("__SPEC__", repr(FLOW_SPEC)),
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
            login = c.post("/api/v1/auth/login",
                           json={"username": env["DATAMASON_ADMIN_USERNAME"],
                                 "password": env["DATAMASON_ADMIN_PASSWORD"]})
            assert login.status_code == 200, login.text

            lists = c.get("/api/v1/deployments").json()
            demo = next((d for d in lists if d["biz_name"].startswith("M34流程E2E")), None)
            assert demo is not None, f"未找到 M34 演示部署: {lists}"
            did = demo["id"]
            assert demo["role"] == "creator", demo
            print(f"[m34] demo deployment id={did} role=creator", flush=True)

            itf = c.get(f"/api/v1/deployments/{did}/interfaces").json()
            crud = next(p for p in itf["pages"] if p["kind"] == "crud_list")
            steps = [
                {"name": "收货登记", "page_id": crud["id"],
                 "done_when": {"type": "records", "min": 1},
                 "status_field": "status", "status_value": "待质检"},
                {"name": "质检", "page_id": crud["id"],
                 "done_when": {"type": "records", "min": 1},
                 "status_field": "status", "status_value": "已质检"},
                {"name": "入库确认", "page_id": crud["id"],
                 "done_when": {"type": "records", "min": 1},
                 "status_field": "status", "status_value": "已入库"},
            ]

            # --- 创建流程 → 生成 kind=flow 流程页 ---
            resp = c.post(f"/api/v1/deployments/{did}/flows",
                          json={"name": "入库流程", "steps": steps})
            assert resp.status_code == 201, resp.text
            flow = resp.json()
            fid = flow["id"]
            flow_page = flow["page_id"]
            assert flow_page, flow
            itf2 = c.get(f"/api/v1/deployments/{did}/interfaces").json()
            assert next(p for p in itf2["pages"] if p["id"] == flow_page)["flow_id"] == fid
            print(f"[m34] 流程创建 OK id={fid} 步骤={[s['name'] for s in flow['steps']]}", flush=True)

            # --- 挂菜单 kind=flow ---
            nodes = [
                {"id": m["id"], "label": m["label"], "kind": m["kind"],
                 "ref_id": m["ref_id"], "parent_id": m["parent_id"]}
                for m in itf2["menus"]
            ]
            nodes.append({"label": "入库流程向导", "kind": "flow",
                          "ref_id": flow_page, "parent_id": None})
            sm = c.put(f"/api/v1/deployments/{did}/menus", json={"nodes": nodes})
            assert sm.status_code == 200, sm.text
            saved = sm.json()["nodes"]
            flow_menu = next(m for m in saved if m["kind"] == "flow")
            print(f"[m34] 菜单 kind=flow 挂载 OK menu_id={flow_menu['id']}", flush=True)

            # --- 运行态：条件未满足 → 推进 409 ---
            rt = c.get(f"/api/v1/app/{did}/flows/{fid}").json()
            assert rt["done"] is False and rt["current_index"] == 0, rt
            assert rt["steps"][0]["state"] == "current" and rt["steps"][0]["met"] is False, rt
            assert c.post(f"/api/v1/app/{did}/flows/{fid}/advance").status_code == 409
            print("[m34] 完成条件不满足 → 不可推进（409）OK", flush=True)

            # --- 录入记录 → 逐步推进，状态字段随步推进 ---
            pk = c.post(f"/api/v1/app/{did}/api/tables/goods/rows",
                        json={"name": "水泥"}).json()["id"]
            assert c.get(f"/api/v1/app/{did}/flows/{fid}").json()["steps"][0]["met"] is True

            expected = ["待质检", "已质检", "已入库"]
            for i, want in enumerate(expected):
                adv = c.post(f"/api/v1/app/{did}/flows/{fid}/advance")
                assert adv.status_code == 200, adv.text
                assert adv.json()["current_index"] == i + 1, adv.json()
                row = c.get(f"/api/v1/app/{did}/api/tables/goods/rows/{pk}").json()
                assert row["status"] == want, (i, row, want)
                print(f"[m34] 第 {i + 1} 步推进 → 状态={want}", flush=True)
            assert c.get(f"/api/v1/app/{did}/flows/{fid}").json()["done"] is True
            assert c.post(f"/api/v1/app/{did}/flows/{fid}/advance").status_code == 409
            print("[m34] 状态随步推进 + 完成后 409 OK", flush=True)

            # --- activity_logs(action=flow) 每步一条（接口倒序，按 id 正序核对） ---
            logs = c.get(f"/api/v1/app/{did}/api/activity-logs",
                         params={"page_size": 100}).json()["logs"]
            step_logs = sorted(
                [lg for lg in logs
                 if lg["action"] == "flow" and "step_name" in lg["changes"]],
                key=lambda lg: lg["id"],
            )
            assert [lg["changes"]["step_name"] for lg in step_logs] == ["收货登记", "质检", "入库确认"], step_logs
            print("[m34] 每步写 activity_logs(action=flow) OK", flush=True)

            # --- 操作员授权后可见；未授权 403 ---
            c.post("/api/v1/auth/users",
                   json={"username": "op_m34", "password": "pw", "display_name": "操作员"})
            c.post(f"/api/v1/deployments/{did}/members",
                   json={"username": "op_m34", "role": "operator",
                         "menu_ids": [flow_menu["id"], *[m["id"] for m in itf2["menus"]
                                                         if m["ref_id"] == crud["id"]]]})
            with httpx.Client(base_url=BASE, timeout=30, follow_redirects=True) as c2:
                c2.post("/api/v1/auth/login", json={"username": "op_m34", "password": "pw"})
                assert c2.get(f"/api/v1/app/{did}/flows/{fid}").status_code == 200
            c.post("/api/v1/auth/users",
                   json={"username": "op2_m34", "password": "pw", "display_name": "操作员乙"})
            c.post(f"/api/v1/deployments/{did}/members",
                   json={"username": "op2_m34", "role": "operator"})
            with httpx.Client(base_url=BASE, timeout=30, follow_redirects=True) as c3:
                c3.post("/api/v1/auth/login", json={"username": "op2_m34", "password": "pw"})
                assert c3.get(f"/api/v1/app/{did}/flows/{fid}").status_code == 403
            print("[m34] 操作员授权可见 / 未授权 403 OK", flush=True)

            # --- 删除流程 → 菜单与流程页一并清理 ---
            assert c.delete(f"/api/v1/deployments/{did}/flows/{fid}").status_code == 200
            itf3 = c.get(f"/api/v1/deployments/{did}/interfaces").json()
            assert all(p["id"] != flow_page for p in itf3["pages"]), itf3["pages"]
            assert all(m["ref_id"] != flow_page for m in itf3["menus"]), itf3["menus"]
            assert c.delete(f"/api/v1/deployments/{did}/flows/{fid}").status_code == 404
            print("[m34] 删除流程清理菜单/流程页 OK", flush=True)

        print("HTTP_CHECK_M34_OK", flush=True)
        return 0
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=5)
        except subprocess.TimeoutExpired:
            srv.kill()


if __name__ == "__main__":
    sys.exit(main())