"""真实 HTTP 点验 M1-4 表编辑器（进程内 werkzeug + 真实 LLM 建一次部署）。

流程：health→bootstrap→建真实模型→preview→confirm→blueprint
     → 字段 add/rename/change_type 一批 → 浏览确认新列
     → 保护列（created_by）删除应 400
用法：带 DATAMASON_ADMIN_* / DATAMASON_LLM_* 环境变量运行。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from werkzeug.serving import make_server  # noqa: E402

HOST, PORT = "127.0.0.1", 8765
BASE = f"http://{HOST}:{PORT}/api/v1"


def call(method, path, data=None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(
        BASE + path, data=body, method=method,
        headers={"Content-Type": "application/json"})

    def _parse(raw):
        t = raw.decode(errors="replace").strip()
        if not t:
            return {"_empty": True}
        try:
            return json.loads(t)
        except json.JSONDecodeError:
            return {"_non_json": t[:300]}

    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.status, _parse(r.read())
    except urllib.error.HTTPError as e:
        return e.code, _parse(e.read())


def wait_health(t=30.0):
    end = time.time() + t
    while time.time() < end:
        try:
            with urllib.request.urlopen(f"{BASE}/health", timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.4)
    return False


def main():
    from server.app import create_app
    srv = make_server(HOST, PORT, create_app(), threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    if not wait_health():
        srv.shutdown()
        print("SERVER_NOT_READY")
        return 2

    ok = True
    try:
        call("POST", "/auth/bootstrap")
        burl = os.environ.get("DATAMASON_LLM_BASE_URL")
        akey = os.environ.get("DATAMASON_LLM_API_KEY")
        if burl and akey:
            st, m = call("POST", "/settings/models", {
                "name": "ark真实", "base_url": burl,
                "model_name": os.environ.get("DATAMASON_LLM_MODEL", "ark-code-latest"),
                "api_key": akey, "temperature": 0})
            if st == 201:
                call("POST", f"/settings/models/{m['id']}/default")

        # 真实建一个进销存
        desc = "做一个简易进销存：商品表记录名称和单价，入库单记录商品、数量、经办人、入库时间"
        t0 = time.time()
        st, proposal = call("POST", "/deployments/preview", {"description": desc})
        print(f"preview {st} {time.time()-t0:.1f}s")
        assert st == 200, proposal
        st, result = call("POST", "/deployments/confirm", {
            "spec_json": proposal["spec_json"], "biz_name": "编辑器点验",
            "description": desc, "seed_data": True})
        print("confirm", st, result.get("table_count"), result.get("status"))
        assert st == 201, result
        did = result["deployment_id"]

        # 选商品表（含 name/单价 的那张）
        st, bp = call("GET", f"/deployments/{did}/blueprint")
        assert st == 200, bp
        target = next(t for t in bp if any(c["name"] == "unit_price" for c in t["columns"]))
        tname = target["name"]
        print("target table:", tname, "cols=", [c["name"] for c in target["columns"]])

        # 一批：加备注 + 名称改名 + 改名后加长到 200
        ops = [
            {"action": "add", "name": "remark", "biz_name": "备注", "type": "TEXT"},
            {"action": "rename", "name": "name", "new_name": "product_name"},
            {"action": "change_type", "name": "product_name",
             "type": "VARCHAR", "length": 200},
        ]
        st, changed = call("POST", f"/deployments/{did}/tables/{tname}/columns",
                           {"ops": ops})
        print("alter", st, [c["name"] for c in changed.get("columns", [])])
        assert st == 200, changed
        names = [c["name"] for c in changed["columns"]]
        assert "remark" in names and "product_name" in names and "name" not in names
        pn = next(c for c in changed["columns"] if c["name"] == "product_name")
        assert pn["length"] == 200

        # 保护列删除必须 400
        st, denied = call("POST", f"/deployments/{did}/tables/{tname}/columns",
                          {"ops": [{"action": "drop", "name": "created_by"}]})
        print("drop ownership ->", st, denied.get("error"))
        assert st == 400

        # 浏览：新列出现、旧名消失、数据仍在
        st, rows = call("GET", f"/deployments/{did}/tables/{tname}/rows?page=1&page_size=3")
        print("browse total=", rows["total"], "has product_name=",
              "product_name" in rows["columns"], "has remark=",
              "remark" in rows["columns"], "old name gone=",
              "name" not in rows["columns"])
        assert "product_name" in rows["columns"]
        assert "remark" in rows["columns"]
        assert rows["total"] == 10
        print("EDITOR_CHECK_OK")
    except AssertionError as exc:
        ok = False
        print("EDITOR_CHECK_FAIL", exc)
    finally:
        srv.shutdown()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
