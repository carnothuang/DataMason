"""部署管线（设计细化 §十一·六）：一验证一部署。

创建模式（M1）：
  preview: 一句话 → LLM 产 table_spec → 确定性派生页面清单（不落库）
  confirm: 自动建 SQLite 物理库 + 登记 connections（消化走查 B1）
           → 执行 DDL（含归属列）→ 回读 → 写 table_specs/page_specs/menus
           → 演示种子（DR-08，可关）→ 注册入口 → running
身份：M1 认证未接入，owner_id 取首个平台管理员（向导创建）；接会话后替换。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from server.dataaccess.manager import execute_ddl
from server.ddl.ddl_builder import build_schema_ddl, inject_ownership_columns
from server.llm.structure import generate_schema, generate_seed_rows
from server.models import (
    Connection,
    CrudConfig,
    Deployment,
    DeploymentMember,
    DeploymentMenu,
    PageSpec,
    TableSpec,
    User,
)


class DeployError(RuntimeError):
    pass


def _session(app):
    return app.extensions["datamason_session_factory"]()


def _slugify(text_zh: str) -> str:
    """从业务名生成英文短路径；中文等非 ASCII 用 dm_ 前缀兜底。"""
    ascii_part = re.sub(r"[^a-zA-Z0-9]+", "_", text_zh).strip("_").lower()
    return ascii_part[:24] if ascii_part else ""


def _owner_id(s) -> int:
    """部署归属：M2-1 起取当前会话用户（登录由 app.py 鉴权拦截层保证）。

    无请求上下文（如脚本直接调 service）时回退到首个平台管理员，兼容 M0 链路脚本。
    """
    try:
        from flask import has_request_context, session

        if has_request_context():
            uid = session.get("user_id")
            if uid:
                user = s.get(User, uid)
                if user is not None:
                    return user.id
    except ImportError:  # pragma: no cover
        pass
    user = s.query(User).filter_by(is_platform_admin=True).order_by(User.id).first()
    if user is None:
        user = s.query(User).order_by(User.id).first()
    if user is None:
        raise DeployError("尚未创建任何用户，请先完成首次启动向导")
    return user.id


# ---------------- 提案（预览，不落业务库） ----------------

def preview(app, description: str) -> dict:
    from server.settings.service import get_default_llm_client

    client = get_default_llm_client(app)
    if client is None:
        raise DeployError("尚未配置默认 LLM 模型，请先到「设置 → 模型管理」添加并测试")

    schema, meta = generate_schema(client, description)
    pages = _derive_pages(schema)
    return {
        "biz_notes": schema.biz_notes,
        "attempts": meta["attempts"],
        # spec_json 由前端在「就这么部署」时原样回传，保证所见即所建
        "spec_json": schema.model_dump(mode="json"),
        "tables": [
            {
                "name": t.name,
                "biz_name": t.biz_name or t.name,
                "columns": [
                    {"name": c.name, "biz_name": c.biz_name, "type": c.type,
                     "is_pk": c.is_pk, "is_fk": c.is_fk,
                     "ref_table": c.ref_table}
                    for c in inject_ownership_columns(t).columns
                ],
            }
            for t in schema.tables
        ],
        "pages": pages,
    }


def _derive_pages(schema, with_public_forms: bool = True) -> list[dict]:
    """确定性派生页面清单（DR-07）：每张表一张 crud_list；
    名字像留言/反馈/提交的表额外派生一张 public_form。
    直连模式（M2-7）不开公开写表单：with_public_forms=False 外部库只做成员 CRUD。"""
    pages = []
    public_keywords = ("留言", "反馈", "消息", "message", "guestbook", "feedback")
    for t in schema.tables:
        pages.append({
            "table": t.name,
            "title": f"{t.biz_name or t.name}管理",
            "kind": "crud_list",
            "access": "member",
        })
        hint = f"{t.name} {t.biz_name}".lower()
        if with_public_forms and any(k.lower() in hint for k in public_keywords):
            pages.append({
                "table": t.name,
                "title": f"我要{t.biz_name or t.name}",
                "kind": "public_form",
                "access": "public",
            })
    return pages


# ---------------- 确认部署（落库） ----------------

def confirm(app, spec_dict: dict, biz_name: str, seed_data: bool = True,
            purpose: str = "", data_mode: str = "create",
            connection_id: int | None = None) -> dict:
    """按提案预览时的 spec 确定性落库（不再调 LLM，保证所见即所建）。

    data_mode="create"（默认）：自建 SQLite 独立库 + 建表 + 可选种子；
    data_mode="link"（M2-7 直连）：复用已有连接的外部库，只登记元数据，
    不建库/不执行 DDL/不种种子（DR-05 绝不 ALTER 外部库）。
    """
    from server.ddl.table_spec import SchemaSpec

    link_mode = data_mode == "link"
    schema = SchemaSpec.model_validate(spec_dict)

    with _session(app) as s:
        owner = _owner_id(s)

        if link_mode:
            # ①' 直连模式：复用已有连接（不新建），参考库由调用方保证存在
            if connection_id is None:
                raise DeployError("直连模式必须指定已有连接")
            conn = s.get(Connection, connection_id)
            if conn is None:
                raise DeployError(f"数据连接不存在（id={connection_id}）")
        else:
            # ① 建独立 SQLite 物理库并登记 connections（B1：创建模式自动建连接）
            slug = _slugify(biz_name) or "dm"
            unique = f"{slug}_{int(datetime.now(timezone.utc).timestamp()) % 100000}"
            rel_db = f"deployments/{unique}.db"
            abs_db = Path(app.config["DATA_ROOT"]) / rel_db
            abs_db.parent.mkdir(parents=True, exist_ok=True)
            uri = f"sqlite:///{abs_db.as_posix()}"

            conn = Connection(
                name=f"[部署]{biz_name}",
                dialect="sqlite",
                conn_uri=app.extensions["cipher"].encrypt(uri),
                status="ok",
            )
            s.add(conn)
            s.flush()

        # ② 部署元数据行（先 deploying）
        deployment = Deployment(
            biz_name=biz_name,
            purpose=purpose or biz_name,
            data_mode=data_mode,
            connection_id=conn.id,
            db_name=conn.name if link_mode else rel_db,
            status="deploying",
            entry_path="",
            owner_id=owner,
            seed_data=seed_data and not link_mode,
        )
        s.add(deployment)
        s.flush()
        deployment_id = deployment.id
        # M2-5：创建人即部署第一位成员（三层角色 creator），成员管理的基础
        s.add(DeploymentMember(
            deployment_id=deployment_id, user_id=owner, role="creator",
        ))
        s.commit()  # 必须提交：后续新会话要按外键引用 connection/deployment

    if link_mode:
        # ③' 直连模式：解析外部库 engine（只读反射），不建表不执行 DDL
        from server.settings.service import get_connection_engine_factory

        biz_engine = get_connection_engine_factory(app)(connection_id)
        statements: list[str] = []
    else:
        # ③ 物理建表（独立连接，避免元数据会话内跨库）
        biz_engine = create_engine(uri, future=True)
        statements = build_schema_ddl(schema, "sqlite")
        execute_ddl(biz_engine, statements)

    # ④ 回读真实 schema（以数据库为准），写元数据
    insp = inspect(biz_engine)
    table_spec_ids: dict[str, int] = {}

    def _rich_columns(table_model, include_ownership: bool = True):
        """富化列蓝图（编辑器 M1-4 直接可用）：含主键/外键/归属/业务名标记。

        以注入归属列后的规格为准（link 模式不注入、以真实列准），
        并用真实反射校准 nullable/类型字符串。
        """
        full = inject_ownership_columns(table_model) if include_ownership else table_model
        real_types: dict[str, str] = {}
        if table_model.name in insp.get_table_names():
            for rc in insp.get_columns(table_model.name):
                real_types[rc["name"]] = str(rc["type"])
        out = []
        for c in full.columns:
            out.append({
                "name": c.name,
                "biz_name": c.biz_name or "",
                "type": c.type,
                "length": c.length,
                "nullable": c.nullable,
                "is_pk": c.is_pk,
                "autoincrement": c.autoincrement,
                "is_fk": c.is_fk,
                "ref_table": c.ref_table,
                "ref_column": c.ref_column,
                "is_ownership": c.is_ownership,
                "db_type": real_types.get(c.name, ""),
            })
        return out

    with _session(app) as s:
        for t in schema.tables:
            rich_cols = _rich_columns(t, include_ownership=not link_mode)
            ts = TableSpec(
                deployment_id=deployment_id,
                name=t.name,
                biz_name=t.biz_name,
                columns_json=json.dumps(rich_cols, ensure_ascii=False),
                ddl_sql=("\n".join(stmt for stmt in statements if t.name in stmt)
                         if not link_mode else ""),
                status="applied",
            )
            s.add(ts)
            s.flush()
            table_spec_ids[t.name] = ts.id

        # ⑤ 页面清单 + 菜单（ref_id → page_specs.id，闭环 B2）
        page_ids: dict[str, int] = {}
        pages = _derive_pages(schema, with_public_forms=not link_mode)
        for p in pages:
            ps = PageSpec(
                deployment_id=deployment_id,
                kind=p["kind"],
                access=p["access"],
                table_id=table_spec_ids[p["table"]],
                title=p["title"],
                layout_json="{}",
            )
            s.add(ps)
            s.flush()
            page_ids[f"{p['table']}:{p['kind']}"] = ps.id
            # 每张 crud_list 自动挂一个菜单项
            if p["kind"] == "crud_list":
                s.add(DeploymentMenu(
                    deployment_id=deployment_id,
                    label=p["title"],
                    kind="table",
                    ref_id=ps.id,
                ))
            # CRUD 配置占位（M2 完整化）
            s.add(CrudConfig(
                deployment_id=deployment_id,
                table_id=table_spec_ids[p["table"]],
                api_config_json="{}",
                ui_config_json="{}",
            ))
        s.commit()

    # ⑥ 演示种子（DR-08；直连模式不种种子，外部库数据是真实数据）
    if seed_data and not link_mode:
        seeds = generate_seed_rows(schema, rows_per_table=10)
        with biz_engine.begin() as conn_db:
            for table_name, rows in seeds.items():
                if not rows:
                    continue
                cols = list(rows[0].keys())
                col_sql = ", ".join(cols)
                ph = ", ".join(f":{c}" for c in cols)
                for row in rows:
                    conn_db.execute(
                        text(f"INSERT INTO {table_name} ({col_sql}) VALUES ({ph})"),
                        row,
                    )
    biz_engine.dispose()

    # ⑦ 注册入口 → running
    entry_path = f"/app/{deployment_id}"
    with _session(app) as s:
        d = s.get(Deployment, deployment_id)
        d.status = "running"
        d.entry_path = entry_path
        s.commit()

    # ⑧ 系统动作日志（M2-6）：部署就绪（actor=触发用户，无请求上下文时=创建人）
    from server.audit.service import log_system

    log_system(
        app, deployment_id, "deploy",
        actor_user_id=_owner_actor_id(app, deployment_id),
        object_kind="deployment", object_pk=str(deployment_id),
        object_label=f"{biz_name} 部署就绪（{'直连模式' if link_mode else '创建模式'} · {len(schema.tables)} 张表）",
        changes={"data_mode": data_mode, "table_count": len(schema.tables),
                 "seeded": seed_data and not link_mode},
    )

    return {
        "deployment_id": deployment_id,
        "biz_name": biz_name,
        "status": "running",
        "entry_path": entry_path,
        "table_count": len(schema.tables),
        "seeded": seed_data and not link_mode,
        "data_mode": data_mode,
    }


def _owner_actor_id(app, deployment_id: int) -> int:
    """部署日志触发者：请求上下文优先取会话用户，否则取部署 owner。"""
    uid = _session_user_id()
    if uid is not None:
        return uid
    with _session(app) as s:
        d = s.get(Deployment, deployment_id)
        return d.owner_id if d is not None else 1


def list_deployments(app) -> list[dict]:
    with _session(app) as s:
        rows = (
            s.query(Deployment)
            .order_by(Deployment.id.desc())
            .all()
        )
        # M2-5：控制台只列我参与的部署（创建人或成员）；无会话上下文（脚本）全列
        uid = _session_user_id()
        if uid is not None:
            member_ids = {
                r.deployment_id
                for r in s.query(DeploymentMember).filter_by(user_id=uid).all()
            }
            rows = [d for d in rows if d.owner_id == uid or d.id in member_ids]
        return [
            {
                "id": d.id,
                "biz_name": d.biz_name,
                "purpose": d.purpose,
                "status": d.status,
                "entry_path": d.entry_path,
                "seed_data": d.seed_data,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in rows
        ]


def _session_user_id() -> int | None:
    """当前会话用户 id；无请求上下文（脚本直调 service）返回 None。"""
    try:
        from flask import has_request_context, session

        if has_request_context():
            return session.get("user_id")
    except ImportError:  # pragma: no cover
        pass
    return None
