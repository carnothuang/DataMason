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
import secrets
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from server.dataaccess.manager import execute_ddl
from server.ddl.ddl_builder import build_schema_ddl, inject_ownership_columns
from server.llm.structure import generate_schema, generate_seed_rows
from server.models import (
    ActivityLog,
    AiGeneration,
    Attachment,
    Connection,
    CrudConfig,
    Deployment,
    DeploymentApiKey,
    DeploymentMember,
    DeploymentMenu,
    DeploymentMenuGrant,
    DeploymentSeedRow,
    DeploymentShareToken,
    FlowSpec,
    FormFieldConfig,
    FormLayout,
    FormOptionItem,
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


def _db_name_from_uri(uri: str | None) -> str:
    """从连接串取实际库名（直连模式列表/导出展示用）。"""
    if not uri:
        return ""
    if uri.startswith("sqlite"):
        tail = uri.split("sqlite:///", 1)[-1] if "sqlite:///" in uri else uri
        return Path(tail).name or tail
    path = uri.rsplit("/", 1)[-1]
    return path.split("?", 1)[0]


def _split_scheme_host(uri: str) -> tuple[str, str]:
    """拆 MySQL/PG 连接串为 (scheme, 'user:pw@host:port')，忽略库名。"""
    scheme, rest = uri.split("://", 1)
    host = rest.rsplit("/", 1)[0] if "/" in rest else rest
    return scheme, host


def _gen_db_name(biz_name: str) -> str:
    slug = _slugify(biz_name) or "dm"
    return (f"dm_{slug}_"
            f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_"
            f"{secrets.token_hex(3)}")


def create_target_database(app, host: Connection, biz_name: str) -> tuple[str, str, str]:
    """在既有 MySQL/PG 服务器连接上新建独立库（A3：创建模式落 MySQL/PG）。

    返回 (新库连接串, 库名, 方言)。SQLite 请走本地文件分支。
    """
    from server.dataaccess.manager import create_database_ddl

    if host.dialect not in ("mysql", "postgresql"):
        raise DeployError("创建模式目标库仅支持 MySQL/PostgreSQL（SQLite 走本地文件）")
    base_uri = app.extensions["cipher"].decrypt(host.conn_uri)
    scheme, hostpart = _split_scheme_host(base_uri)
    db_name = _gen_db_name(biz_name)

    if host.dialect == "mysql":
        admin_uri = f"{scheme}://{hostpart}"
        engine = create_engine(admin_uri, future=True)
        try:
            with engine.begin() as conn_db:
                conn_db.execute(text(create_database_ddl("mysql", db_name)))
        finally:
            engine.dispose()
        new_uri = f"{admin_uri}/{db_name}"
    else:  # postgresql：连维护库 postgres（CREATE DATABASE 不能落事务）
        admin_uri = f"{scheme}://{hostpart}/postgres"
        engine = create_engine(admin_uri, future=True).execution_options(
            isolation_level="AUTOCOMMIT")
        try:
            with engine.connect() as conn_db:
                conn_db.execute(text(create_database_ddl("postgresql", db_name)))
        finally:
            engine.dispose()
        new_uri = f"{scheme}://{hostpart}/{db_name}"
    return new_uri, db_name, host.dialect


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
    from server.ai import service as ai_service
    from server.settings.service import get_default_llm_client

    client = get_default_llm_client(app)
    if client is None:
        raise DeployError("尚未配置默认 LLM 模型，请先到「设置 → 模型管理」添加并测试")

    try:
        schema, meta = generate_schema(client, description)
    except Exception as exc:  # noqa: BLE001 失败也留一条历史（治理页可见）
        ai_service.record(app, "deploy", "error", prompt_summary=description,
                          result_summary=f"{type(exc).__name__}: {exc}")
        raise
    ai_service.record(
        app, "deploy", "ok", prompt_summary=description,
        result_summary=f"{schema.biz_notes or description}（{len(schema.tables)} 表，"
                       f"attempts={meta['attempts']}）",
        usage=meta.get("usage"))
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


# ---------------- M6 混合建库：两段式（骨架 / 字段，DR-35） ----------------

def propose_skeleton(app, description: str, mode: str = "create") -> dict:
    """混合建库 Step 2：一句话 → 表骨架 + 外键列占位（不落库，DR-37）。

    仅创建模式开放（DR-39，直连外部库只读）；LLM 只产表集合 + 关系，
    Python 确定性映射外键列占位（DR-35）。骨架与占位列回传前端草稿。
    """
    from server.ai import service as ai_service
    from server.ddl.skeleton import map_relations_to_columns
    from server.llm.structure import generate_skeleton
    from server.settings.service import get_default_llm_client

    if mode == "link":
        raise DeployError("混合建库仅支持创建模式（直连外部库只读，DR-39）")
    client = get_default_llm_client(app)
    if client is None:
        raise DeployError("尚未配置默认 LLM 模型，请先到「设置 → 模型管理」添加并测试")

    try:
        skeleton, meta = generate_skeleton(client, description)
    except Exception as exc:  # noqa: BLE001 失败也记一条，便于排查
        ai_service.record(app, "deploy", "error", prompt_summary=description,
                          result_summary=f"骨架 {type(exc).__name__}: {exc}")
        raise

    ai_service.record(
        app, "deploy", "ok", prompt_summary=description,
        result_summary=f"表骨架：{skeleton.biz_notes or description}"
                       f"（{len(skeleton.tables)} 表，attempts={meta['attempts']}）",
        usage=meta.get("usage"))
    columns = {
        name: [c.model_dump(mode="json") for c in cols]
        for name, cols in map_relations_to_columns(skeleton).items()
    }
    return {
        "biz_notes": skeleton.biz_notes,
        "attempts": meta["attempts"],
        "skeleton_json": skeleton.model_dump(mode="json"),
        "columns": columns,
    }


def generate_columns(app, skeleton_dict: dict, table_name: str, nl: str,
                     mode: str = "overwrite", existing_columns=None,
                     guided_tables=None) -> dict:
    """混合建库 Step 3：逐表 NL 引导字段（骨架上下文注入，DR-35/38）。

    mode=overwrite 整表重写；append 在既有字段上追加。返回该表完整字段 +
    仍未引导字段的表清单（供 Step 4 拦截）。
    """
    from pydantic import ValidationError

    from server.ai import service as ai_service
    from server.ddl.skeleton import SchemaSkeleton
    from server.llm.structure import generate_table_columns
    from server.settings.service import get_default_llm_client

    try:
        skeleton = SchemaSkeleton.model_validate(skeleton_dict)
    except ValidationError as exc:
        raise DeployError(f"骨架不合法：{exc}") from exc
    if table_name not in skeleton.table_map:
        raise DeployError(f"表 {table_name} 不在骨架中")

    client = get_default_llm_client(app)
    if client is None:
        raise DeployError("尚未配置默认 LLM 模型，请先到「设置 → 模型管理」添加并测试")

    try:
        columns, meta = generate_table_columns(
            client, skeleton, table_name, nl, mode=mode,
            existing_columns=existing_columns)
    except Exception as exc:  # noqa: BLE001
        ai_service.record(app, "deploy", "error", prompt_summary=nl,
                          result_summary=f"字段 {table_name} {type(exc).__name__}: {exc}")
        raise

    ai_service.record(
        app, "deploy", "ok", prompt_summary=nl,
        result_summary=f"字段：{table_name}（{len(columns)} 列，"
                       f"mode={mode}，attempts={meta['attempts']}）",
        usage=meta.get("usage"))

    guided = set(guided_tables or [])
    guided.add(table_name)
    unguided = [t for t in skeleton.table_names if t not in guided]
    return {
        "table": table_name,
        "attempts": meta["attempts"],
        "columns": [c.model_dump(mode="json") for c in columns],
        "unguided_tables": unguided,
    }


# ---------------- M6-5 混合建库：合并为完整 spec（复用落库链路） ----------------

def merge_hybrid(app, skeleton_dict: dict, columns_map: dict) -> dict:
    """骨架 + 各表字段 → 完整 spec_json（供前端直接复用 confirm 落库）。

    校验失败抛 `SkeletonMergeError`（携带定位到表/字段的 errors 清单）。
    """
    from pydantic import ValidationError

    from server.ddl.skeleton import SchemaSkeleton, merge_skeleton_columns

    try:
        skeleton = SchemaSkeleton.model_validate(skeleton_dict)
    except ValidationError as exc:
        raise DeployError(f"骨架不合法：{exc}") from exc
    if not isinstance(columns_map, dict):
        raise DeployError("columns 必须是「表名 → 字段列表」映射")

    spec = merge_skeleton_columns(skeleton, columns_map)
    return {
        "biz_notes": spec.biz_notes,
        "table_count": len(spec.tables),
        "spec_json": spec.model_dump(mode="json"),
    }


# ---------------- 确认部署（落库） ----------------


def confirm(app, spec_dict: dict, biz_name: str, seed_data: bool = True,
            purpose: str = "", data_mode: str = "create",
            connection_id: int | None = None,
            target_connection_id: int | None = None) -> dict:
    """按提案预览时的 spec 确定性落库（不再调 LLM，保证所见即所建）。

    data_mode="create"（默认）：自建独立库 + 建表 + 可选种子；
      默认落 SQLite 文件；给定 target_connection_id（MySQL/PG 服务器连接）时
      在该服务器上新建独立库后落库（A3）。
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
            link_db_name = _db_name_from_uri(app.extensions["cipher"].decrypt(conn.conn_uri))
            biz_dialect = conn.dialect
        elif target_connection_id is not None:
            # ①'' 创建模式（A3）：在既有 MySQL/PG 服务器连接上新建独立库
            host = s.get(Connection, target_connection_id)
            if host is None:
                raise DeployError(f"目标服务器连接不存在（id={target_connection_id}）")
            uri, rel_db, biz_dialect = create_target_database(app, host, biz_name)
            conn = Connection(
                name=f"[部署]{biz_name}", dialect=biz_dialect,
                conn_uri=app.extensions["cipher"].encrypt(uri), status="ok",
            )
            s.add(conn)
            s.flush()
        else:
            # ① 建独立 SQLite 物理库并登记 connections（B1：创建模式自动建连接）
            slug = _slugify(biz_name) or "dm"
            unique = (
                f"{slug}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
                f"_{secrets.token_hex(3)}"
            )
            rel_db = f"deployments/{unique}.db"
            abs_db = Path(app.config["DATA_ROOT"]) / rel_db
            abs_db.parent.mkdir(parents=True, exist_ok=True)
            uri = f"sqlite:///{abs_db.as_posix()}"
            biz_dialect = "sqlite"

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
            db_name=link_db_name if link_mode else rel_db,
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
        # ③ 物理建表（独立连接，避免元数据会话内跨库）；方言随目标库
        biz_engine = create_engine(uri, future=True)
        statements = build_schema_ddl(schema, biz_dialect)
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
        menu_index = 0
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
            # 每张 crud_list 自动挂一个菜单项（M3-2 起带同层排序号，拖拽重组用）
            if p["kind"] == "crud_list":
                s.add(DeploymentMenu(
                    deployment_id=deployment_id,
                    label=p["title"],
                    kind="table",
                    ref_id=ps.id,
                    order_index=menu_index,
                ))
                menu_index += 1
            # CRUD 配置占位（M2 完整化）
            s.add(CrudConfig(
                deployment_id=deployment_id,
                table_id=table_spec_ids[p["table"]],
                api_config_json="{}",
                ui_config_json="{}",
            ))
        s.commit()

    # ⑥ 演示种子（DR-08；直连模式不种种子，外部库数据是真实数据）
    seed_records: list[tuple[str, str]] = []
    if seed_data and not link_mode:
        seeds = generate_seed_rows(schema, rows_per_table=10)
        pk_names = {
            t.name: next((c.name for c in t.columns if c.is_pk), None)
            for t in schema.tables
        }
        with biz_engine.begin() as conn_db:
            for table_name, rows in seeds.items():
                if not rows:
                    continue
                cols = list(rows[0].keys())
                col_sql = ", ".join(cols)
                ph = ", ".join(f":{c}" for c in cols)
                pk_name = pk_names.get(table_name)
                for row in rows:
                    conn_db.execute(
                        text(f"INSERT INTO {table_name} ({col_sql}) VALUES ({ph})"),
                        row,
                    )
                    if pk_name and row.get(pk_name) is not None:
                        seed_records.append((table_name, str(row[pk_name])))
        # 登记种子行主键：供「一键清空演示数据」精确删除（DR-08）
        if seed_records:
            with _session(app) as s:
                for table_name, pk in seed_records:
                    s.add(DeploymentSeedRow(
                        deployment_id=deployment_id, table_name=table_name, row_pk=pk))
                s.commit()
    biz_engine.dispose()

    # ⑦ 注册入口 → running
    entry_path = f"/app/{deployment_id}"
    with _session(app) as s:
        d = s.get(Deployment, deployment_id)
        d.status = "running"
        d.entry_path = entry_path
        s.commit()

    # M4-4：种子数据生成也留一条 AI 生成历史（DR-08，确定性生成，无 token 消耗）
    if seed_data and not link_mode:
        from server.ai import service as ai_service

        rows_total = sum(len(v) for v in seeds.values())
        ai_service.record(
            app, "seed", "ok", prompt_summary=f"{biz_name} 演示数据",
            result_summary=f"{len(seeds)} 张表 · {rows_total} 行（确定性，DR-08）",
            deployment_id=deployment_id)

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


# ---------------- M3-1 生命周期：停止 / 再启动 ----------------

def set_lifecycle(app, deployment_id: int, target: str, actor_user_id: int) -> dict:
    """M3-1 stop（running→stopped）/ start（stopped→running）。

    权限：创建人独有（DR-14），由路由层 _require_creator 保证；
    数据保留不删、不碰业务库，仅切状态；系统动作 stop/start 埋点（M3-1）。
    """
    from server.audit.service import log_system

    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        d = s.get(Deployment, deployment_id)
        if d is None:
            raise DeployError("部署不存在")
        if target == "stop":
            if d.status != "running":
                raise DeployError(f"仅运行中的部署可停止（当前：{d.status}）")
        elif target == "start":
            if d.status != "stopped":
                raise DeployError(f"仅已停止的部署可再启动（当前：{d.status}）")
        else:
            raise DeployError("未知生命周期动作")
        d.status = "stopped" if target == "stop" else "running"
        s.commit()
        status, biz_name, entry_path = d.status, d.biz_name, d.entry_path

    log_system(
        app, deployment_id, target,
        actor_user_id=actor_user_id,
        object_kind="deployment", object_pk=str(deployment_id),
        object_label=f"{biz_name} {'已停止' if target == 'stop' else '已再启动'}",
        changes={"status": status, "entry_path": entry_path},
    )
    return {"deployment_id": deployment_id, "biz_name": biz_name,
            "status": status, "entry_path": entry_path}


# ---------------- DR-08 一键清空演示数据 ----------------

def clear_seed_data(app, deployment_id: int, actor_user_id: int) -> dict:
    """按登记主键删除演示种子行（只删种子、不动用户真实数据）。

    - 创建模式专有；直连模式无种子 → 400。
    - 逆登记序删除（子表先删，规避外键）；被用户数据引用的种子行跳过并计数。
    - 全部清完则把 deployments.seed_data 置 False；系统动作 seed 埋点。
    """
    from server.audit.service import log_system

    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        d = s.get(Deployment, deployment_id)
        if d is None:
            raise DeployError("部署不存在")
        if d.data_mode == "link":
            raise DeployError("直连模式没有演示数据可清空")
        biz_name = d.biz_name
        conn = s.get(Connection, d.connection_id) if d.connection_id else None
        uri = app.extensions["cipher"].decrypt(conn.conn_uri) if conn is not None else None
        # 主键列名：从 table_specs 蓝图取
        pk_by_table: dict[str, str] = {}
        for ts in s.query(TableSpec).filter_by(deployment_id=deployment_id).all():
            for c in json.loads(ts.columns_json or "[]"):
                if c.get("is_pk"):
                    pk_by_table[ts.name] = c["name"]
                    break
        records = [
            (r.id, r.table_name, r.row_pk)
            for r in s.query(DeploymentSeedRow)
            .filter_by(deployment_id=deployment_id)
            .order_by(DeploymentSeedRow.id)
            .all()
        ]

    if not records:
        return {"deployment_id": deployment_id, "deleted": 0, "failed": 0,
                "tables": 0, "already_empty": True}

    assert uri is not None  # 创建模式必有连接
    engine = create_engine(uri, future=True)
    deleted = 0
    failed = 0
    deleted_ids: list[int] = []
    try:
        for rec_id, table_name, pk in reversed(records):
            pk_col = pk_by_table.get(table_name)
            if not pk_col:
                continue
            try:
                with engine.begin() as conn_db:
                    res = conn_db.execute(
                        text(f'DELETE FROM "{table_name}" WHERE "{pk_col}" = :pk'),
                        {"pk": pk},
                    )
                if (res.rowcount or 0) > 0:
                    deleted += 1
                    deleted_ids.append(rec_id)
                else:
                    deleted_ids.append(rec_id)  # 行已不存在，登记可清
            except Exception:  # noqa: BLE001 被用户数据引用等，跳过该行
                failed += 1
    finally:
        engine.dispose()

    with Session() as s:
        if deleted_ids:
            s.query(DeploymentSeedRow).filter(
                DeploymentSeedRow.id.in_(deleted_ids)
            ).delete(synchronize_session=False)
        if failed == 0:
            d = s.get(Deployment, deployment_id)
            if d is not None:
                d.seed_data = False
        s.commit()

    log_system(
        app, deployment_id, "seed",
        actor_user_id=actor_user_id,
        object_kind="deployment", object_pk=str(deployment_id),
        object_label=f"{biz_name} 清空演示数据",
        changes={"deleted": deleted, "failed": failed, "tables": len(pk_by_table)},
    )
    return {"deployment_id": deployment_id, "deleted": deleted, "failed": failed,
            "tables": len(pk_by_table), "already_empty": False}


# ---------------- M3-7 销毁（创建人独有，DR-14） ----------------

def _sqlite_file_from_uri(uri: str | None) -> str | None:
    if not uri or not uri.startswith("sqlite"):
        return None
    if ":memory:" in uri:
        return None
    tail = uri.split("sqlite:///", 1)[-1] if "sqlite:///" in uri else uri.split("sqlite://", 1)[-1]
    return tail or None


def destroy_deployment(app, deployment_id: int, actor_user_id: int) -> dict:
    """销毁部署（创建人独有，路由层保证）：二次确认/先导出由前端引导。

    - 创建模式：删独立 SQLite 物理库文件（限 DATA_ROOT 内）+ 自动登记的连接（无他部署引用时）；
    - 直连模式：**绝不删外部库与连接**，只清元数据（红线同 M2-7/M2-8）；
    - 元数据级联清除：菜单/授权/页面/流程/表/成员/CRUD 配置/分享链接/操作日志/deployment 行；
    - destroy 埋点随销毁清（导出包在销毁前已带走全量日志，DR-03/§五）。
    """
    cipher = app.extensions["cipher"]
    data_root = Path(app.config["DATA_ROOT"]).resolve()
    remove_file: str | None = None
    remove_conn_id: int | None = None
    with _session(app) as s:
        d = s.get(Deployment, deployment_id)
        if d is None:
            raise DeployError("部署不存在")
        biz_name, data_mode = d.biz_name, d.data_mode
        if data_mode == "create" and d.connection_id:
            conn = s.get(Connection, d.connection_id)
            if conn is not None:
                path = _sqlite_file_from_uri(cipher.decrypt(conn.conn_uri))
                if path:
                    resolved = Path(path).resolve()
                    if resolved == data_root or data_root in resolved.parents:
                        remove_file = str(resolved)  # 仅在 DATA_ROOT 内才删（防误删）
                others = (
                    s.query(Deployment)
                    .filter(Deployment.connection_id == conn.id,
                            Deployment.id != deployment_id)
                    .count()
                )
                if others == 0:
                    remove_conn_id = conn.id
        # 级联清除：先子后父（尊重外键依赖：menus→pages→flows→tables；deployment→connection）
        menu_ids = [
            mid for (mid,) in s.query(DeploymentMenu.id)
            .filter_by(deployment_id=deployment_id).all()
        ]
        if menu_ids:
            s.query(DeploymentMenuGrant).filter(
                DeploymentMenuGrant.menu_id.in_(menu_ids)
            ).delete(synchronize_session=False)
        s.query(DeploymentMenu).filter_by(deployment_id=deployment_id).delete(synchronize_session=False)
        s.query(DeploymentShareToken).filter_by(deployment_id=deployment_id).delete(synchronize_session=False)
        s.query(DeploymentApiKey).filter_by(deployment_id=deployment_id).delete(synchronize_session=False)
        s.query(DeploymentMember).filter_by(deployment_id=deployment_id).delete(synchronize_session=False)
        s.query(ActivityLog).filter_by(deployment_id=deployment_id).delete(synchronize_session=False)
        s.query(AiGeneration).filter_by(deployment_id=deployment_id).delete(synchronize_session=False)
        s.query(Attachment).filter_by(deployment_id=deployment_id).delete(synchronize_session=False)
        # 0.13.0 M7-0：表单控件配置随部署级联清除（业务库零 DDL，仅元数据）
        s.query(FormFieldConfig).filter_by(deployment_id=deployment_id).delete(synchronize_session=False)
        s.query(FormOptionItem).filter_by(deployment_id=deployment_id).delete(synchronize_session=False)
        s.query(FormLayout).filter_by(deployment_id=deployment_id).delete(synchronize_session=False)
        s.query(CrudConfig).filter_by(deployment_id=deployment_id).delete(synchronize_session=False)
        s.query(PageSpec).filter_by(deployment_id=deployment_id).delete(synchronize_session=False)
        s.query(FlowSpec).filter_by(deployment_id=deployment_id).delete(synchronize_session=False)
        s.query(TableSpec).filter_by(deployment_id=deployment_id).delete(synchronize_session=False)
        s.delete(d)
        s.flush()
        if remove_conn_id is not None:
            conn = s.get(Connection, remove_conn_id)
            if conn is not None:
                s.delete(conn)
        s.commit()

    file_removed = False
    if remove_file:
        try:
            Path(remove_file).unlink(missing_ok=True)
            file_removed = True
        except OSError:
            file_removed = False
    # 0.11.0 D-1：删除该部署附件目录（每部署独立；仅限 DATA_ROOT 内，防误删）
    files_dir_removed = False
    deploy_dir = data_root / "deployments"
    target_dir = (deploy_dir / f"deployment_{deployment_id}.files").resolve()
    if deploy_dir in target_dir.parents:
        try:
            if target_dir.exists():
                import shutil

                shutil.rmtree(target_dir)
                files_dir_removed = True
        except OSError:
            files_dir_removed = False
    return {
        "deployment_id": deployment_id,
        "biz_name": biz_name,
        "data_mode": data_mode,
        "destroyed": True,
        "db_file_removed": file_removed,
        "files_dir_removed": files_dir_removed,
        "external_preserved": data_mode != "create",
    }


# ---------------- M3-2 界面与菜单：工程室（创建人独有，DR-14） ----------------

_ENTRY_RE = re.compile(r"^\/[A-Za-z0-9][A-Za-z0-9_\-]*(?:\/[A-Za-z0-9_\-]+)*$", re.ASCII)
_ENTRY_RESERVED = {"/app", "/login", "/deployments", "/settings", "/assets", "/api"}


def get_interfaces(app, deployment_id: int) -> dict:
    """工程室「界面与菜单」数据：菜单树（平铺，含排序）+ 页面卡 + 已授权菜单 id + 入口路径。"""
    with _session(app) as s:
        d = s.get(Deployment, deployment_id)
        if d is None:
            raise DeployError("部署不存在")
        menus = (
            s.query(DeploymentMenu)
            .filter_by(deployment_id=deployment_id)
            .order_by(DeploymentMenu.order_index, DeploymentMenu.id)
            .all()
        )
        granted = {
            g.menu_id
            for g in s.query(DeploymentMenuGrant)
            .join(DeploymentMenu, DeploymentMenuGrant.menu_id == DeploymentMenu.id)
            .filter(DeploymentMenu.deployment_id == deployment_id)
            .all()
        }
        specs = {
            t.id: t for t in s.query(TableSpec).filter_by(deployment_id=deployment_id).all()
        }
        pages = (
            s.query(PageSpec)
            .filter_by(deployment_id=deployment_id)
            .order_by(PageSpec.id)
            .all()
        )
        return {
            "menus": [
                {"id": m.id, "label": m.label, "kind": m.kind,
                 "ref_id": m.ref_id, "parent_id": m.parent_id}
                for m in menus
            ],
            "pages": [
                {"id": p.id, "kind": p.kind, "access": p.access, "title": p.title,
                 "flow_id": p.flow_id,
                 "table": specs[p.table_id].name if p.table_id and p.table_id in specs else None,
                 "table_biz_name": specs[p.table_id].biz_name
                 if p.table_id and p.table_id in specs else None}
                for p in pages
            ],
            "granted_menu_ids": sorted(granted),
            "entry_path": d.entry_path or f"/app/{deployment_id}",
        }


def save_menus(app, deployment_id: int, nodes: list[dict], actor_user_id: int) -> dict:
    """整组保存菜单树（即拖即存）：把前端拖拽后的菜单列表按序落库。

    - 有 id → 复用该部署既有菜单行（校验归属）；无 id → 新建（界面卡拖入/新建分组）。
    - parent_id：正数=既有菜单 id；负数=-1/-2… 表示本列表第 1/2 个节点（新建分组当日拖入，
      一条请求即可建组并收纳子节点）；缺失/0 → 根。
    - 按列表位置重排 order_index；支持建分组/改父子/增删/挪动；自校验环。
    - 变更走 structure 埋点（M3-2）；空 diff 不写日志。
    """
    from server.audit.service import log_system

    if not isinstance(nodes, list):
        raise DeployError("menus 必须是菜单节点列表")
    if len(nodes) > 500:
        raise DeployError("菜单节点过多")

    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        d = s.get(Deployment, deployment_id)
        if d is None:
            raise DeployError("部署不存在")
        existing = {
            m.id: m
            for m in s.query(DeploymentMenu).filter_by(deployment_id=deployment_id).all()
        }
        old_state = {
            m.id: (m.order_index, m.parent_id) for m in existing.values()
        }
        page_ids = {
            pid for (pid,) in s.query(PageSpec.id)
            .filter_by(deployment_id=deployment_id).all()
        }

        reused: set[int] = set()
        new_rows: list[DeploymentMenu] = []
        items: list[tuple[dict, DeploymentMenu]] = []
        for node in nodes:
            if not isinstance(node, dict):
                raise DeployError("菜单节点格式错误")
            label = (node.get("label") or "").strip()
            if not label or len(label) > 128:
                raise DeployError("菜单名称必须为 1-128 字符")
            kind = node.get("kind") or "table"
            if kind not in ("table", "flow", "dashboard"):
                raise DeployError("不支持的菜单类型")
            ref_id = node.get("ref_id")
            if ref_id is not None and ref_id not in page_ids:
                raise DeployError("菜单指向的页面不属于该部署")
            nid = node.get("id")
            if nid is not None:
                if nid not in existing or nid in reused:
                    raise DeployError("存在不属于该部署或重复的菜单 id")
                row = existing[nid]
                reused.add(nid)
            else:
                row = DeploymentMenu(deployment_id=deployment_id)
                s.add(row)
                new_rows.append(row)
            row.label = label
            row.kind = kind
            row.ref_id = ref_id
            items.append((node, row))
        s.flush()

        # 解析父引用：正数=既有菜单 id；负数=-1 表示列表中第 1 个节点（父先子后）；
        # 事务内自校验环，保证有向无环。
        def _resolve_parent(node: dict, item_rows: list[DeploymentMenu]) -> int | None:
            pid = node.get("parent_id")
            if pid is None or pid == 0:
                return None
            if isinstance(pid, int) and pid < 0:
                idx = -pid - 1
                if idx >= len(item_rows):
                    raise DeployError("parent_id 索引超出节点列表")
                return item_rows[idx].id
            if pid not in existing or pid not in reused:
                return None  # 父被移除/未出现在本次提交 → 孤儿升级为根
            return pid

        item_id: list[int] = []
        for idx, (node, row) in enumerate(items):
            row.parent_id = _resolve_parent(node, [r for _n, r in items])
            row.order_index = idx
            item_id.append(row.id)

        # 环检测：沿父链上溯，超过节点数即存在环（异常时事务回滚，不落库）
        for node, row in items:
            cur = row.id
            seen = {cur}
            probe = row
            for _ in range(len(items) + 1):
                if probe.parent_id is None:
                    break
                probe = next(r for _n, r in items if r.id == probe.parent_id)
                if probe.id in seen:
                    raise DeployError("菜单父子关系存在环")
                seen.add(probe.id)

        removed_labels: list[str] = []
        for mid, mrow in existing.items():
            if mid in reused:
                continue
            removed_labels.append(mrow.label)
            s.query(DeploymentMenuGrant).filter_by(menu_id=mid).delete()
            s.delete(mrow)
        s.commit()

        snapshot = [
            {"id": r.id, "label": r.label, "kind": r.kind,
             "ref_id": r.ref_id, "parent_id": r.parent_id}
            for _n, r in items
        ]
        added_labels = [r.label for r in new_rows]
        reordered = any(
            old_state.get(row.id) != (row.order_index, row.parent_id)
            for _n, row in items
            if _n.get("id") is not None
        )
        changed = bool(added_labels or removed_labels or reordered)
        if changed:
            label = "菜单调整" + (f"：新增「{'、'.join(added_labels)}」" if added_labels else "")
            if removed_labels:
                label += f"，移除「{'、'.join(removed_labels)}」"
            log_system(
                app, deployment_id, "structure", actor_user_id=actor_user_id,
                object_kind="menu", object_table="deployment_menus",
                object_pk=str(deployment_id),
                object_label=label or "菜单调整",
                changes={"action": "menu_layout",
                         "added": added_labels,
                         "removed": removed_labels,
                         "reordered": reordered,
                         "menu_count": len(items)},
            )
    return {"ok": True, "nodes": snapshot}


def update_page_title(app, deployment_id: int, page_id: int, title: str,
                      actor_user_id: int) -> dict:
    """界面卡「调整」：改页面标题（页面编辑器整体在后续里程碑）。"""
    from server.audit.service import log_system

    title = (title or "").strip()
    if not title or len(title) > 128:
        raise DeployError("页面标题必须为 1-128 字符")
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        p = s.get(PageSpec, page_id)
        if p is None or p.deployment_id != deployment_id:
            raise DeployError("页面不存在")
        old = p.title
        p.title = title
        s.commit()
    log_system(
        app, deployment_id, "structure", actor_user_id=actor_user_id,
        object_kind="page", object_table="page_specs", object_pk=str(page_id),
        object_label=f"界面标题调整：{old} → {title}",
        changes={"action": "page_title", "old": old, "new": title},
    )
    return {"ok": True, "page_id": page_id, "title": title}


def set_entry_path(app, deployment_id: int, path: str, actor_user_id: int) -> dict:
    """自定义入口路径（M3-2/DR-15）：留言板 → /guestbook；留空恢复 /app/{id}。

    旧路径在改后不再是入口（/app/{id} 失配 → 前端 404 提示「入口已迁移」）。
    """
    from server.audit.service import log_system

    path = (path or "").strip()
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        d = s.get(Deployment, deployment_id)
        if d is None:
            raise DeployError("部署不存在")
        default_path = f"/app/{deployment_id}"
        if not path or path == default_path or path.startswith("/app/"):
            new_path = default_path
        else:
            if len(path) > 64 or not _ENTRY_RE.match(path):
                raise DeployError("入口路径须为 /字母数字_/ 组成（如 /guestbook），最长 64 字符")
            if path in _ENTRY_RESERVED or any(
                path.startswith(f"{r}/") for r in _ENTRY_RESERVED
            ):
                raise DeployError(f"「{path}」为保留路径，请换一个")
            dup = (
                s.query(Deployment)
                .filter(Deployment.entry_path == path,
                        Deployment.id != deployment_id)
                .first()
            )
            if dup is not None:
                raise DeployError(f"入口路径已被「{dup.biz_name}」占用")
            new_path = path
        old = d.entry_path
        if new_path != old:
            d.entry_path = new_path
            s.commit()
            log_system(
                app, deployment_id, "structure", actor_user_id=actor_user_id,
                object_kind="deployment", object_table="deployments",
                object_pk=str(deployment_id),
                object_label=f"入口路径调整：{old or '未设置'} → {new_path}",
                changes={"action": "entry_path", "old": old, "new": new_path},
            )
        biz_name = d.biz_name
    return {"deployment_id": deployment_id, "entry_path": new_path, "biz_name": biz_name}


def resolve_entry_path(app, path: str) -> dict:
    """自定义路径解析（M3-2）：匹配 entry_path → {found, deployment_id, ...}。

    非成员不返回（避免泄露部署存在性）；无匹配 → found=false。
    """
    from server.members import service as member_service

    with _session(app) as s:
        d = s.query(Deployment).filter(Deployment.entry_path == path).first()
        if d is None:
            return {"found": False}
        uid = _session_user_id()
        if uid is None:
            return {"found": False}  # 匿名不解析（不泄露部署存在性）
        role = member_service.get_role(s, d, uid)
        if role is None:
            return {"found": False}
        return {
            "found": True,
            "deployment_id": d.id,
            "entry_path": d.entry_path,
            "status": d.status,
            "biz_name": d.biz_name,
            "role": role,
        }


def list_deployments(app) -> list[dict]:
    with _session(app) as s:
        rows = (
            s.query(Deployment)
            .order_by(Deployment.id.desc())
            .all()
        )
        # M2-5：控制台只列我参与的部署（创建人或成员）；无会话上下文（脚本）全列
        uid = _session_user_id()
        member_rows: list[DeploymentMember] = []
        if uid is not None:
            member_rows = (
                s.query(DeploymentMember).filter_by(user_id=uid).all()
            )
            member_ids = {r.deployment_id for r in member_rows}
            rows = [d for d in rows if d.owner_id == uid or d.id in member_ids]
        # M3-1：按人标记角色（DR-14 停止/再启动=创建人独有，前端据此裁剪按钮）
        role_map = {r.deployment_id: r.role for r in member_rows}
        return [
            {
                "id": d.id,
                "biz_name": d.biz_name,
                "purpose": d.purpose,
                "status": d.status,
                "entry_path": d.entry_path,
                "data_mode": d.data_mode,
                "seed_data": d.seed_data,
                "role": ("creator" if d.owner_id == uid else role_map.get(d.id))
                if uid is not None else None,
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
