"""真实配置中心点验：进程内起服务，HTTP 验证 SQLite 直建 / MySQL 连接 / Redis。

覆盖：
- POST /settings/connections (sqlite, create=true) → test
- POST /settings/connections (mysql, DATAMASON_MYSQL_* 环境变量, 不带库) → test
- PUT  /settings/redis (启用 DATAMASON_REDIS_HOST/PORT) → POST /redis/test(PING) → GET 回读
用法：
  DATAMASON_MYSQL_PASSWORD=xxx python scripts/run_settings_check.py
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
sys.path.insert(0, str(ROOT / "src"))

from werkzeug.serving import make_server  # noqa: E402

HOST, PORT = "127.0.0.1", 8765
BASE = f"http://{HOST}:{PORT}/api/v1"

MYSQL = {
    "host": os.environ.get("DATAMASON_MYSQL_HOST", "127.0.0.1"),
    "port": int(os.environ.get("DATAMASON_MYSQL_PORT", "3306")),
    "user": os.environ.get("DATAMASON_MYSQL_USER", "root"),
    "password": os.environ.get("DATAMASON_MYSQL_PASSWORD", ""),
}
REDIS = {
    "host": os.environ.get("DATAMASON_REDIS_HOST", "127.0.0.1"),
    "port": int(os.environ.get("DATAMASON_REDIS_PORT", "6379")),
    "db": int(os.environ.get("DATAMASON_REDIS_DB", "0")),
}


def call(method: str, path: str, data=None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(
        BASE + path, data=body, method=method,
        headers={"Content-Type": "application/json"},
    )

    def _parse(raw: bytes):
        text = raw.decode(errors="replace").strip()
        if not text:
            return {"_empty": True}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"_non_json": text[:500]}

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, _parse(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, _parse(exc.read())


def wait_for_health(timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{BASE}/health", timeout=3) as resp:
                if resp.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            time.sleep(0.4)
    return False


def main() -> int:
    from datamason.app import create_app

    app = create_app()
    server = make_server(HOST, PORT, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    if not wait_for_health():
        server.shutdown()
        print("server not ready")
        return 2

    ok = True
    try:
        # 1. SQLite 直建
        st, res = call("POST", "/settings/connections", {
            "name": "点验SQLite", "dialect": "sqlite",
            "db_path": "settings_check.db", "create": True,
        })
        print("sqlite create:", st, res)
        cid = res.get("id")
        st, test = call("POST", f"/settings/connections/{cid}/test")
        print("sqlite test:", st, test)
        ok &= bool(test.get("ok"))

        # 2. MySQL 真实连接（指向实例，不带库）
        st, res = call("POST", "/settings/connections", {
            "name": "点验MySQL", "dialect": "mysql",
            "host": MYSQL["host"], "port": MYSQL["port"],
            "user": MYSQL["user"], "password": MYSQL["password"],
        })
        print("mysql create:", st, res)
        mid = res.get("id")
        st, test = call("POST", f"/settings/connections/{mid}/test")
        print("mysql test:", st, test)
        ok &= bool(test.get("ok"))

        # 3. Redis 启用 + 测试 + 回读掩码
        st, res = call("PUT", "/settings/redis", {
            "enabled": True, "host": REDIS["host"],
            "port": REDIS["port"], "db": REDIS["db"],
        })
        print("redis save:", st, res)
        st, test = call("POST", "/settings/redis/test")
        print("redis ping:", st, test)
        ok &= bool(test.get("ok"))
        st, back = call("GET", "/settings/redis")
        print("redis readback:", st, back)
    finally:
        server.shutdown()

    print("SETTINGS_CHECK_OK" if ok else "SETTINGS_CHECK_FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
