"""M8（0.14.0）真浏览器 E2E 专用服务：在「隔离临时环境」启动，复用 serve_for_e2e 的种子与托管。

与通用 serve_for_e2e.py 的唯一差异：不写真实元数据库，而是用注入的 Config
指向 .workbuddy/tmp/e2e_m8/ 下的独立元数据库与 data 目录，保证：
  - 平台无任何历史用户/部署，管理员凭证由 DATAMASON_ADMIN_* 决定
  - 不设 DATAMASON_LLM_* 时天然无任何 LLM 模型
  - 多次运行幂等（沿用同一隔离目录；如需全新环境删除该目录即可）
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "e2e"))

from datamason.config import Config  # noqa: E402

ISOLATED = ROOT / ".workbuddy" / "tmp" / "e2e_m8"
ISOLATED.mkdir(parents=True, exist_ok=True)
META_DB = ISOLATED / "meta.db"
DATA_ROOT = ISOLATED / "data"
DATA_ROOT.mkdir(parents=True, exist_ok=True)


def main() -> int:
    from datamason.app import create_app
    import serve_for_e2e as base

    cfg = Config(
        meta_db_uri=f"sqlite:///{META_DB.resolve().as_posix()}",
        data_root=str(DATA_ROOT.resolve()),
        secret_key="e2e-m8-isolated-secret",
    )
    app = create_app(cfg)

    dist = ROOT / "dashboard" / "dist"
    if not dist.exists():
        print("[serve] dashboard/dist 不存在，请先 npm run build", flush=True)

    base.seed(app)
    if os.environ.get("DATAMASON_E2E_LLM_STUB") == "1":
        base._install_stub_llm(app)
    base._seed_external(app)

    run = base._run_server(app)
    print(f"[serve-m8] isolated meta={META_DB}", flush=True)
    print(f"[serve-m8] listening http://{base.HOST}:{base.PORT}", flush=True)
    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
