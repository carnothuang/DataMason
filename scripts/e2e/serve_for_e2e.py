"""M1-6 真浏览器 E2E 专用服务：进程内 werkzeug + 应用内直接预置管理员与真实 LLM 模型。

凭证从环境变量读（绝不硬编码）。直接在 app context 内调用 service，
不走 HTTP 自连接（避免 serve_forever 前无 accept、以及本机代理拦截 127.0.0.1）。
启动后在 8765 提供「Flask API + web/dist 单页应用」，供 Playwright 同源访问。
环境变量：
  DATAMASON_ADMIN_USERNAME / DATAMASON_ADMIN_PASSWORD
  DATAMASON_LLM_BASE_URL / DATAMASON_LLM_API_KEY / DATAMASON_LLM_MODEL
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from werkzeug.serving import make_server  # noqa: E402

HOST, PORT = "127.0.0.1", 8765


def seed(app) -> None:
    from server.auth.service import hash_password
    from server.models import User
    from server.settings import service as settings_service

    with app.app_context():
        Session = app.extensions["datamason_session_factory"]
        with Session() as s:
            if s.query(User).count() == 0:
                s.add(User(
                    username=os.environ.get("DATAMASON_ADMIN_USERNAME", "admin"),
                    display_name="管理员",
                    password_hash=hash_password(
                        os.environ.get("DATAMASON_ADMIN_PASSWORD", "admin123")),
                    is_platform_admin=True,
                ))
                s.commit()

        burl = os.environ.get("DATAMASON_LLM_BASE_URL")
        akey = os.environ.get("DATAMASON_LLM_API_KEY")
        if burl and akey:
            existing = settings_service.list_models(app)
            if not existing:
                m = settings_service.create_model(app, {
                    "name": "E2E真实模型",
                    "base_url": burl,
                    "model_name": os.environ.get("DATAMASON_LLM_MODEL", "ark-code-latest"),
                    "api_key": akey,
                    "temperature": 0,
                })
                settings_service.set_default_model(app, m["id"])
                print(f"[seed] model created id={m['id']} set default", flush=True)
            else:
                print("[seed] model already exists, skip", flush=True)


def main() -> int:
    from server.app import create_app

    app = create_app()
    dist = ROOT / "web" / "dist"
    if not dist.exists():
        print("[serve] web/dist 不存在，请先 npm run build", flush=True)
    seed(app)
    srv = make_server(HOST, PORT, app, threaded=True)
    print(f"[serve] listening http://{HOST}:{PORT}", flush=True)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
