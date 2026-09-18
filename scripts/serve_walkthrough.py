"""本地功能走查/演示服务：waitress 正式 WSGI，托管 Flask API + web/dist 单页应用。

用法（项目根目录，managed venv）：
    python scripts/serve_walkthrough.py            # 0.0.0.0:8765
    python scripts/serve_walkthrough.py 9000       # 指定端口

说明：与 scripts/e2e/serve_for_e2e.py 的区别——本脚本不做 E2E 预置、不装 LLM 桩，
用真实元数据库与真实 LLM 配置（如需 NL 能力请在设置页配好模型）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    from server.app import create_app

    if not (ROOT / "web" / "dist" / "index.html").exists():
        print("[serve] 未找到 web/dist，请先在 web/ 执行 npm run build", flush=True)

    app = create_app()
    try:
        from waitress import serve
    except ImportError:
        from werkzeug.serving import make_server

        srv = make_server("0.0.0.0", port, app, threaded=True)
        print(f"[serve] werkzeug http://0.0.0.0:{port}", flush=True)
        srv.serve_forever()
        return 0
    print(f"[serve] waitress http://0.0.0.0:{port}", flush=True)
    serve(app, host="0.0.0.0", port=port, threads=16)
    return 0


if __name__ == "__main__":
    sys.exit(main())
