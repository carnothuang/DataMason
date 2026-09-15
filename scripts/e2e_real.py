"""真实端到端：HTTP + 真实 LLM + 真实 SQLite 落库。

前置：后端已在 127.0.0.1:8765 运行，LLM 环境变量已配置（可选；也可直接用已入库模型）。
本脚本：引导管理员 → 配置真实模型 → preview → confirm → 浏览表数据。
用法：python scripts/e2e_real.py
"""
from __future__ import annotations

import os
import sys
import time
import urllib.error
import urllib.request
import json

BASE = "http://127.0.0.1:8765/api/v1"


def call(method: str, path: str, data=None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(
        BASE + path, data=body, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def main() -> int:
    # 1. 健康检查
    _, health = call("GET", "/health")
    print("health:", health)

    # 2. 引导（幂等）
    _, bootstrap = call("POST", "/auth/bootstrap")
    print("bootstrap:", bootstrap)

    # 3. 配置真实模型（若给了环境变量）
    base_url = os.environ.get("DATAMASON_LLM_BASE_URL")
    api_key = os.environ.get("DATAMASON_LLM_API_KEY")
    model = os.environ.get("DATAMASON_LLM_MODEL", "ark-code-latest")
    if base_url and api_key:
        status, created = call("POST", "/settings/models", {
            "name": "火山方舟(真实)",
            "base_url": base_url,
            "model_name": model,
            "api_key": api_key,
            "temperature": 0,
        })
        print("model create:", status)
        if status == 201:
            tested = call("POST", f"/settings/models/{created['id']}/test")[1]
            print("model test ok:", tested.get("ok"))
            call("POST", f"/settings/models/{created['id']}/default")

    # 4. 真实生成提案
    desc = "做一个简易进销存：商品表记录名称和单价，入库单记录商品、数量、经办人、入库时间"
    t0 = time.time()
    status, proposal = call("POST", "/deployments/preview", {"description": desc})
    print(f"preview status={status} 耗时={time.time()-t0:.1f}s attempts={proposal.get('attempts')}")
    if status != 200:
        print("PREVIEW_FAIL:", proposal)
        return 1
    for t in proposal["tables"]:
        print(f"  表 {t['biz_name']}({t['name']}) 列={[c['name'] for c in t['columns']]}")
    print("  页面:", [(p["title"], p["kind"], p["access"]) for p in proposal["pages"]])

    # 5. 确认部署
    status, result = call("POST", "/deployments/confirm", {
        "spec_json": proposal["spec_json"],
        "biz_name": "进销存E2E",
        "description": desc,
        "seed_data": True,
    })
    print("confirm:", status, result)
    if status != 201:
        return 1
    did = result["deployment_id"]

    # 6. 浏览
    _, tables = call("GET", f"/deployments/{did}/tables")
    print("tables:", [(t["name"], len(t["columns"])) for t in tables])
    first = tables[0]["name"]
    _, rows = call("GET", f"/deployments/{did}/tables/{first}/rows?page=1&page_size=5")
    print(f"rows[{first}] total={rows['total']} sample={rows['data'][:1]}")
    print("E2E_REAL_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
