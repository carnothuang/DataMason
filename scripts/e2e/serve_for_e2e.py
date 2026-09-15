"""M1-6/M2-4 真浏览器 E2E 专用服务：进程内 werkzeug + 应用内直接预置管理员与真实 LLM 模型。

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

# M2-4 真浏览器验收用的确定性演示部署（不经 LLM，直接走 confirm）。
E2E_SPEC = {
    "biz_notes": "E2E：客户与订单（含唯一列/布尔/时间/外键下拉）",
    "tables": [
        {
            "name": "customers",
            "biz_name": "客户",
            "columns": [
                {"name": "id", "biz_name": "主键", "type": "INTEGER",
                 "is_pk": True, "autoincrement": True, "nullable": False},
                {"name": "name", "biz_name": "客户名", "type": "VARCHAR",
                 "length": 64, "nullable": False, "unique": True},
                {"name": "phone", "biz_name": "电话", "type": "VARCHAR", "length": 32},
                {"name": "vip", "biz_name": "VIP", "type": "BOOLEAN"},
                {"name": "joined", "biz_name": "入会", "type": "DATETIME"},
                {"name": "remark", "biz_name": "备注", "type": "TEXT"},
            ],
            "indexes": [],
        },
        {
            "name": "orders",
            "biz_name": "订单",
            "columns": [
                {"name": "id", "biz_name": "主键", "type": "INTEGER",
                 "is_pk": True, "autoincrement": True, "nullable": False},
                {"name": "customer_id", "biz_name": "客户", "type": "INTEGER",
                 "nullable": False, "is_fk": True,
                 "ref_table": "customers", "ref_column": "id"},
                {"name": "amount", "biz_name": "金额", "type": "DECIMAL"},
                {"name": "memo", "biz_name": "说明", "type": "VARCHAR", "length": 255},
            ],
            "indexes": [],
        },
    ],
}


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

        # M2-4：元数据库为空则预置一个确定性演示部署（绕过 LLM，供 CRUD UI 真浏览器验收）
        from server.deploy import service as deploy_service
        from server.models import Deployment

        with Session() as s:
            has_demo = s.query(Deployment).filter(
                Deployment.biz_name.like("客户与订单E2E%")).count()
            if not has_demo:
                result = deploy_service.confirm(
                    app, E2E_SPEC, biz_name="客户与订单E2E", seed_data=False)
                print(
                    f"[seed] deployment_id={result['deployment_id']} "
                    f"entry_path={result['entry_path']}",
                    flush=True,
                )
            else:
                print("[seed] demo deployment already exists, skip", flush=True)


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
