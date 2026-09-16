"""M1-6/M2-4/M2-9 真浏览器 E2E 专用服务：进程内 werkzeug + 应用内直接预置管理员与真实 LLM 模型。

凭证从环境变量读（绝不硬编码）。直接在 app context 内调用 service，
不走 HTTP 自连接（避免 serve_forever 前无 accept、以及本机代理拦截 127.0.0.1）。
启动后在 8765 提供「Flask API + web/dist 单页应用」，供 Playwright 同源访问。
环境变量：
  DATAMASON_ADMIN_USERNAME / DATAMASON_ADMIN_PASSWORD
  DATAMASON_LLM_BASE_URL / DATAMASON_LLM_API_KEY / DATAMASON_LLM_MODEL
  DATAMASON_E2E_LLM_STUB=1  使用确定性 LLM 桩（M2-9：部署向导全流程免真实模型）
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from werkzeug.serving import make_server  # noqa: E402  # noqa: F401 回退路径使用

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


def _install_stub_llm(app) -> None:
    """M2-9：部署向导的确定性 LLM 桩（DATAMASON_E2E_LLM_STUB=1 时启用）。

    只把 settings_service.get_default_llm_client 换成桩，其余管线原样走真实路径
    （generate_schema 的 pydantic 自检、confirm 落库/种子/日志都在线）。桩无视描述，
    恒定返回 E2E_SPEC；结构生成与补标签两种调用（chat_json）都能被该形状满足。
    """
    from server.llm.client import Usage

    class _StubLlm:
        def chat_json(self, system_prompt: str, user_prompt: str):
            # M4-1 问数据：只读 SQL / 一句话结论（桩无视问题，返回确定性查询）
            if "只读 SQL" in system_prompt:
                return ({"sql": "SELECT id, name, vip FROM customers ORDER BY id",
                         "title": "客户列表"}, Usage(tokens_in=0, tokens_out=0, duration_ms=1))
            if "总结" in system_prompt:
                return {"conclusion": "已列出当前客户。"}, Usage(
                    tokens_in=0, tokens_out=0, duration_ms=1)
            return E2E_SPEC, Usage(tokens_in=0, tokens_out=0, duration_ms=1)

        def close(self):
            pass

    import server.settings.service as settings_service

    settings_service.get_default_llm_client = lambda _app: _StubLlm()
    print(
        f"[seed] LLM 桩已接入（确定性 {len(E2E_SPEC['tables'])} 表结构）",
        flush=True,
    )


# M2-9 直连验收用确定性外部库：products/suppliers（含 FK；缺归属列 → 降级明示路径）。
E2E_EXTERNAL_DDL = """
CREATE TABLE IF NOT EXISTS suppliers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name VARCHAR(100) NOT NULL,
    region VARCHAR(50)
);
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
    name VARCHAR(64) NOT NULL,
    price DECIMAL(10, 2),
    spec TEXT
);
"""


def _seed_external(app) -> None:
    """登记「E2E外部产品库」连接（不存在才建，幂等）。用于 M2-9 直连模式浏览器验收。"""
    from server.settings import service as settings_service

    with app.app_context():
        conns = settings_service.list_connections(app)
        if any(c["name"] == "E2E外部产品库" for c in conns):
            print("[seed] E2E外部产品库已存在，跳过", flush=True)
            return
        import sqlite3

        db_path = Path(app.config["DATA_ROOT"]) / "e2e_external_products.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path)
        try:
            conn.executescript(E2E_EXTERNAL_DDL)
            conn.commit()
        finally:
            conn.close()
        created = settings_service.create_connection(app, {
            "name": "E2E外部产品库",
            "dialect": "sqlite",
            "db_path": "e2e_external_products.db",
        })
        print(f"[seed] 外部直连库已建 connection_id={created['id']} path={db_path}", flush=True)


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
    if os.environ.get("DATAMASON_E2E_LLM_STUB") == "1":
        _install_stub_llm(app)
    _seed_external(app)
    srv = _run_server(app)
    print(f"[serve] listening http://{HOST}:{PORT}", flush=True)
    srv()
    return 0


def _run_server(app) -> "Callable[[], None]":
    # M2-9：正式 serve 优先 waitress（线程池+keep-alive 健壮），
    # 避免 werkzeug dev server 长连接挂起致真浏览器验收偶发白屏。
    try:
        from waitress import serve as _serve
        return lambda: _serve(app, host=HOST, port=PORT, threads=16)
    except ImportError:  # pragma: no cover - 依赖缺失时回退
        from werkzeug.serving import make_server as _make
        srv = _make(HOST, PORT, app, threaded=True)
        return srv.serve_forever


if __name__ == "__main__":
    sys.exit(main())
