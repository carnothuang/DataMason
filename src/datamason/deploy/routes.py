"""部署路由：部署清单 + 部署流（预览提案 / 确认部署）+ 数据浏览/蓝图（M2-5 接成员权限）。"""
from __future__ import annotations

from flask import Blueprint, abort, current_app, jsonify, request, session
from werkzeug.exceptions import HTTPException

from datamason.crud import runtime
from datamason.audit.service import log_system
from datamason.deploy import link, service
from datamason.deploy.service import DeployError
from datamason.ddl.skeleton import SkeletonMergeError
from datamason.ddl.sql_import import SqlImportError
from datamason.deploy.link import LinkError
from datamason.deploy import editor
from datamason.deploy import structure as structure_service
from datamason.deploy.structure import StructureError
from datamason.members import service as member_service
from datamason.members.service import PermissionDenied
from datamason.models import Deployment, User
from datamason.workspace import service as workspace_service

bp = Blueprint("deploy", __name__)


def _body() -> dict:
    return request.get_json(silent=True) or {}


def _uid() -> int:
    return session.get("user_id") or 1


def _deployment_and_role(app, deployment_id: int) -> tuple[Deployment, str]:
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        d = s.get(Deployment, deployment_id)
        if d is None:
            abort(404, description="部署不存在")
        role = member_service.assert_member(s, d, _uid())
    return d, role


@bp.errorhandler(PermissionDenied)
def _permission_error(exc: PermissionDenied):
    return jsonify({"error": str(exc)}), 403


@bp.errorhandler(HTTPException)
def _http_error(exc: HTTPException):
    return jsonify({"error": exc.description}), exc.code


def _assert_running(deployment_id: int) -> None:
    """控制台数据浏览：停止态部署不可操作（与工作台/API 的 409 门控一致）。"""
    Session = current_app.extensions["datamason_session_factory"]
    with Session() as s:
        d = s.get(Deployment, deployment_id)
        if d is None:
            abort(404, description="部署不存在")
        if d.status != "running":
            abort(409, description="部署已停止，数据暂不可操作；可在部署清单再启动")


@bp.get("/deployments")
def list_deployments():
    return jsonify(service.list_deployments(current_app))


# ---------------- M3-1 生命周期：停止 / 再启动（创建人独有，DR-14） ----------------

@bp.post("/deployments/<int:deployment_id>/stop")
def deployment_stop(deployment_id: int):
    _deployment_and_role(current_app, deployment_id)
    _require_creator(deployment_id)
    try:
        return jsonify(service.set_lifecycle(current_app, deployment_id, "stop", _uid()))
    except service.DeployError as exc:
        return jsonify({"error": str(exc)}), 400


@bp.post("/deployments/<int:deployment_id>/start")
def deployment_start(deployment_id: int):
    _deployment_and_role(current_app, deployment_id)
    _require_creator(deployment_id)
    try:
        return jsonify(service.set_lifecycle(current_app, deployment_id, "start", _uid()))
    except service.DeployError as exc:
        return jsonify({"error": str(exc)}), 400


# ---------------- DR-08 一键清空演示数据（创建人/管理员） ----------------

@bp.post("/deployments/<int:deployment_id>/seed/clear")
def deployment_seed_clear(deployment_id: int):
    """清空演示种子行（按登记主键精确删除，只删种子、不动真实数据）。"""
    _d, role = _deployment_and_role(current_app, deployment_id)
    if role not in ("creator", "admin"):
        return jsonify({"error": "没有清空演示数据的权限（创建人/管理员）"}), 403
    try:
        return jsonify(service.clear_seed_data(current_app, deployment_id, _uid()))
    except service.DeployError as exc:
        return jsonify({"error": str(exc)}), 400


# ---------------- M3-7 销毁（创建人独有，DR-14） ----------------

@bp.post("/deployments/<int:deployment_id>/destroy")
def deployment_destroy(deployment_id: int):
    """销毁部署：创建模式连物理库一起清，直连模式只清元数据（绝不删外部库）；元数据/日志全清。"""
    _deployment_and_role(current_app, deployment_id)
    _require_creator(deployment_id)
    try:
        return jsonify(service.destroy_deployment(current_app, deployment_id, _uid()))
    except service.DeployError as exc:
        return jsonify({"error": str(exc)}), 400


@bp.post("/deployments/preview")
def preview():
    data = _body()
    description = (data.get("description") or "").strip()
    if not description:
        return jsonify({"error": "请描述你要验证什么（一句话）"}), 400
    try:
        return jsonify(service.preview(current_app, description))
    except DeployError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 502


# ---------------- M6 混合建库：两段式骨架/字段（DR-34~39，仅创建模式） ----------------

@bp.post("/deployments/skeleton/propose")
def skeleton_propose():
    """混合建库 Step 2：一句话 → 表骨架 + 外键列占位（不落库，DR-37/39）。"""
    data = _body()
    description = (data.get("description") or "").strip()
    if not description:
        return jsonify({"error": "请描述你要建什么（一句话）"}), 400
    mode = data.get("mode") or "create"
    if mode not in ("create", "link"):
        return jsonify({"error": f"未知数据模式: {mode}"}), 400
    try:
        return jsonify(service.propose_skeleton(current_app, description, mode))
    except DeployError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 502


@bp.post("/deployments/skeleton/tables/<table_name>/columns")
def skeleton_table_columns(table_name: str):
    """混合建库 Step 3：逐表 NL 引导字段（骨架上下文注入；overwrite/append）。"""
    data = _body()
    nl = (data.get("nl") or data.get("description") or "").strip()
    if not nl:
        return jsonify({"error": "请用一句话描述这张表需要哪些字段"}), 400
    mode = data.get("mode") or "overwrite"
    if mode not in ("overwrite", "append"):
        return jsonify({"error": f"未知字段生成模式: {mode}"}), 400
    skeleton = data.get("skeleton")
    if not isinstance(skeleton, dict):
        return jsonify({"error": "缺少骨架 skeleton"}), 400
    try:
        return jsonify(service.generate_columns(
            current_app, skeleton, table_name, nl, mode=mode,
            existing_columns=data.get("existing_columns"),
            guided_tables=data.get("guided_tables")))
    except DeployError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 502


@bp.post("/deployments/skeleton/merge")
def skeleton_merge():
    """混合建库 Step 4：骨架+字段合并为完整 spec_json；失败返回定位清单。"""
    data = _body()
    skeleton = data.get("skeleton")
    columns = data.get("columns")
    if not isinstance(skeleton, dict) or not isinstance(columns, dict):
        return jsonify({"error": "缺少骨架 skeleton 或字段映射 columns"}), 400
    try:
        return jsonify(service.merge_hybrid(current_app, skeleton, columns))
    except DeployError as exc:
        return jsonify({"error": str(exc)}), 400
    except SkeletonMergeError as exc:
        return jsonify({"error": str(exc), "errors": exc.errors}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 502


@bp.post("/deployments/manual/sql/parse")
def manual_sql_parse():
    """专业建库：CREATE TABLE → 结构提案（不执行、不落库、不调 LLM，DR-42/45）。

    仅创建模式开放：mode=link 直接拒绝（直连只读红线）；解析失败返回定位清单。
    """
    data = _body()
    mode = data.get("mode") or "create"
    if mode == "link":
        return jsonify({"error": "专业建库仅支持创建模式（直连外部库只读，DR-45）"}), 400
    if mode != "create":
        return jsonify({"error": f"未知数据模式: {mode}"}), 400
    dialect = data.get("dialect")
    if dialect not in ("sqlite", "mysql", "postgresql"):
        return jsonify({"error": "请选择有效的 SQL 方言（sqlite/mysql/postgresql）"}), 400
    try:
        return jsonify(service.parse_manual_sql(
            current_app, data.get("sql") or "", dialect,
            target_connection_id=_opt_int(data, "target_connection_id", default=None),
        ))
    except SqlImportError as exc:
        return jsonify({"error": str(exc), "errors": exc.errors}), 400
    except DeployError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 502


@bp.post("/deployments/confirm")
def confirm():
    data = _body()
    spec_json = data.get("spec_json")
    biz_name = (data.get("biz_name") or "").strip()
    purpose = (data.get("description") or "").strip()
    mode = data.get("mode") or "create"
    if mode not in ("create", "link"):
        return jsonify({"error": f"未知数据模式: {mode}"}), 400
    if not spec_json or not biz_name:
        return jsonify({"error": "缺少 spec_json 或 biz_name"}), 400
    seed_data = bool(data.get("seed_data", True))
    try:
        result = service.confirm(
            current_app, spec_json, biz_name,
            seed_data=seed_data, purpose=purpose,
            data_mode=mode, connection_id=_opt_int(data, "connection_id",
                                                    default=None),
            target_connection_id=_opt_int(data, "target_connection_id",
                                          default=None),
        )
        return jsonify(result), 201
    except service.DeployError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 502


# ---------------- M3-2 界面与菜单：工程室（创建人独有，DR-14） ----------------

# ---------------- M3-6 配置包导出（创建人/管理员，DR-14；停止态也可导出） ----------------

@bp.get("/deployments/<int:deployment_id>/export")
def deployment_export(deployment_id: int):
    """导出配置包：部署元信息 + 表/页面/菜单/流程规格 + crud_configs + 全量 committed 日志。

    JSON 附件下载，可离线打开；创建人/管理员可导出，操作员/非成员 403。
    """
    from datamason.deploy import export as export_service

    _d, role = _deployment_and_role(current_app, deployment_id)
    if role not in ("creator", "admin"):
        return jsonify({"error": "没有导出权限（创建人/管理员可导出）"}), 403
    try:
        package = export_service.build_package(current_app, deployment_id)
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404
    filename = f"datamason-deploy-{deployment_id}.json"
    resp = jsonify(package)
    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


@bp.get("/deployments/<int:deployment_id>/interfaces")
def deployment_interfaces(deployment_id: int):
    """工程室「界面与菜单」：菜单树（平铺）+ 页面卡 + 已授权菜单 id（前端打「需授权」标）。"""
    _deployment_and_role(current_app, deployment_id)
    _require_creator(deployment_id)
    try:
        return jsonify(service.get_interfaces(current_app, deployment_id))
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404


@bp.put("/deployments/<int:deployment_id>/menus")
def deployment_save_menus(deployment_id: int):
    """拖拽即拖即存：整组菜单树落库（增删/排序/父子），变更走 structure 埋点。"""
    _deployment_and_role(current_app, deployment_id)
    _require_creator(deployment_id)
    data = _body()
    try:
        return jsonify(service.save_menus(
            current_app, deployment_id, data.get("nodes"), _uid()))
    except service.DeployError as exc:
        return jsonify({"error": str(exc)}), 400
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404


@bp.put("/deployments/<int:deployment_id>/pages/<int:page_id>")
def deployment_page_title(deployment_id: int, page_id: int):
    """界面卡「调整」：改页面标题（页面编辑器整体在后续里程碑）。"""
    _deployment_and_role(current_app, deployment_id)
    _require_creator(deployment_id)
    data = _body()
    try:
        return jsonify(service.update_page_title(
            current_app, deployment_id, page_id,
            (data.get("title") or "").strip(), _uid()))
    except service.DeployError as exc:
        return jsonify({"error": str(exc)}), 400


# ---------------- 0.15.0 M9-6 页面布局与表级 UI 配置（工程室） ----------------

def _require_layout_config(deployment_id: int) -> None:
    """布局配置权限：部署 owner/creator 或平台管理员（沿 forms._require_form_config，DR-14）。"""
    Session = current_app.extensions["datamason_session_factory"]
    with Session() as s:
        d = s.get(Deployment, deployment_id)
        if d is None:
            abort(404, description="部署不存在")
        uid = _uid()
        role = member_service.get_role(s, d, uid)
        user = s.get(User, uid)
        if role != "creator" and not (user and user.is_platform_admin):
            abort(403, description="你没有调整页面布局的权限（仅创建人与平台管理员）")


@bp.get("/deployments/<int:deployment_id>/pages/<int:page_id>/layout")
def get_page_layout(deployment_id: int, page_id: int):
    _deployment_and_role(current_app, deployment_id)
    _require_layout_config(deployment_id)
    return jsonify(workspace_service.get_page_layout(
        current_app, deployment_id, page_id))


@bp.put("/deployments/<int:deployment_id>/pages/<int:page_id>/layout")
def save_page_layout(deployment_id: int, page_id: int):
    _deployment_and_role(current_app, deployment_id)
    _require_layout_config(deployment_id)
    try:
        return jsonify(workspace_service.save_page_layout(
            current_app, deployment_id, page_id, _body(), actor_user_id=_uid()))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@bp.delete("/deployments/<int:deployment_id>/pages/<int:page_id>/layout")
def reset_page_layout(deployment_id: int, page_id: int):
    _deployment_and_role(current_app, deployment_id)
    _require_layout_config(deployment_id)
    return jsonify(workspace_service.reset_page_layout(
        current_app, deployment_id, page_id, actor_user_id=_uid()))


@bp.get("/deployments/<int:deployment_id>/tables/<table_name>/ui-config")
def get_table_ui_config(deployment_id: int, table_name: str):
    _deployment_and_role(current_app, deployment_id)
    _require_layout_config(deployment_id)
    return jsonify(workspace_service.get_table_ui_config(
        current_app, deployment_id, table_name))


@bp.put("/deployments/<int:deployment_id>/tables/<table_name>/ui-config")
def save_table_ui_config(deployment_id: int, table_name: str):
    _deployment_and_role(current_app, deployment_id)
    _require_layout_config(deployment_id)
    try:
        return jsonify(workspace_service.save_table_ui_config(
            current_app, deployment_id, table_name, _body(), actor_user_id=_uid()))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@bp.put("/deployments/<int:deployment_id>/entry_path")
def deployment_entry_path(deployment_id: int):
    """自定义入口路径（DR-15）：/guestbook 形态；留空恢复 /app/{id}。旧路径不再暴露。"""
    _deployment_and_role(current_app, deployment_id)
    _require_creator(deployment_id)
    data = _body()
    try:
        return jsonify(service.set_entry_path(
            current_app, deployment_id, data.get("path"), _uid()))
    except service.DeployError as exc:
        return jsonify({"error": str(exc)}), 400


@bp.get("/entry/lookup")
def entry_lookup():
    """自定义入口解析：path=精确匹配 entry_path → {found, deployment_id, ...}。

    非成员/无匹配一律 found=false（不泄露部署存在性）。
    """
    path = (request.args.get("path") or "").strip()
    return jsonify(service.resolve_entry_path(current_app, path))


# ---------------- M2-7 直连模式（接已有的，DR-09/DR-18） ----------------

@bp.get("/deployments/link/connections")
def link_connections():
    """可直连的连接清单：登录即可见（区别于 /settings 连接管理需平台管理员）。"""
    from datamason.settings.service import list_connections

    return jsonify(list_connections(current_app))


@bp.post("/deployments/link/inventory")
def link_inventory():
    data = _body()
    try:
        conn_id = _require_conn_id(data)
        return jsonify(link.inventorize(current_app, conn_id))
    except (LinkError, DeployError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 502


@bp.post("/deployments/link/select")
def link_select():
    """两段式选表：意图一句话 → 相关表推荐（schema 过大时 _select_relevant_tables）。"""
    data = _body()
    purpose = (data.get("purpose") or "").strip()
    if not purpose:
        return jsonify({"error": "请描述你要从已有数据验证什么（一句话）"}), 400
    try:
        conn_id = _require_conn_id(data)
        return jsonify(link.select_recommend(current_app, conn_id, purpose))
    except (LinkError, DeployError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 502


@bp.post("/deployments/link/preview")
def link_preview():
    """直连提案：反向生成 table_spec +（可选）AI 补业务标签 → 复用提案形态。"""
    data = _body()
    try:
        conn_id = _require_conn_id(data)
        tables = data.get("tables") or []
        purpose = (data.get("purpose") or "").strip()
        enhance = bool(data.get("enhance", False))
        return jsonify(link.preview(
            current_app, conn_id, tables, purpose=purpose, enhance=enhance,
        ))
    except (LinkError, DeployError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 502


def _require_conn_id(data: dict) -> int:
    conn_id = _opt_int(data, "connection_id", default=0)
    if not conn_id:
        raise ValueError("缺少 connection_id")
    return conn_id


def _opt_int(data: dict, key: str, default: int | None):
    """从 JSON body 取整数；兼容前端可能以字符串提交数字。"""
    raw = data.get(key, default)
    if raw is None or isinstance(raw, int):
        return raw
    try:
        return int(str(raw))
    except (TypeError, ValueError):
        return default


# ---------------- M1-3 按部署只读浏览 ----------------

_RESERVED = {"page", "page_size", "sort", "order", "search"}


@bp.get("/deployments/<int:deployment_id>/tables")
def deployment_tables(deployment_id: int):
    _deployment_and_role(current_app, deployment_id)  # 成员校验（M2-5）
    _assert_running(deployment_id)  # 停止态不可操作（M3-1 门控一致）
    try:
        return jsonify(runtime.list_tables(current_app, deployment_id, _uid()))
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404


@bp.get("/deployments/<int:deployment_id>/tables/<table_name>/rows")
def deployment_rows(deployment_id: int, table_name: str):
    _deployment_and_role(current_app, deployment_id)  # 成员校验（M2-5）
    _assert_running(deployment_id)  # 停止态不可操作
    page = max(int(request.args.get("page", 1)), 1)
    page_size = min(max(int(request.args.get("page_size", 20)), 1), 200)
    sort = request.args.get("sort")
    order = request.args.get("order", "asc")
    filters = {k: v for k, v in request.args.items() if k not in _RESERVED}
    try:
        return jsonify(runtime.list_rows(
            current_app, deployment_id, table_name,
            page, page_size, sort, order, filters, _uid(),
            search=(request.args.get("search") or "").strip(),
        ))
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 400


# ---------------- M1-4 表编辑器 v0（单表字段增删/改名/改类型） ----------------

@bp.get("/deployments/<int:deployment_id>/blueprint")
def deployment_blueprint(deployment_id: int):
    """返回部署内每张表的富化列蓝图（编辑器渲染用）；工程室=创建人独有（DR-14）。"""
    _deployment_and_role(current_app, deployment_id)
    _require_creator(deployment_id)
    try:
        return jsonify(editor.get_blueprint(current_app, deployment_id))
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404
    except editor.EditorError as exc:
        return jsonify({"error": str(exc)}), 400


# ---------------- M2-8 数据蓝图：结构变更 diff 提案（生成即提案，确认后 ALTER） ----------------

@bp.post("/deployments/<int:deployment_id>/structure/propose")
def propose_structure(deployment_id: int):
    """对单表一组字段操作生成 diff 提案：只算不执行（含方言化 ALTER 语句），
    供蓝图页「diff 提案卡」审查；确认后仍走 alter_columns 落地。工程室=创建人独有（DR-14）。"""
    _deployment_and_role(current_app, deployment_id)
    _require_creator(deployment_id)
    data = _body()
    table_name = (data.get("table") or "").strip()
    ops = data.get("ops")
    if not table_name:
        return jsonify({"error": "缺少 table"}), 400
    if not isinstance(ops, list):
        return jsonify({"error": "ops 必须是操作列表"}), 400
    try:
        return jsonify(editor.propose_table(
            current_app, deployment_id, table_name, ops))
    except editor.EditorError as exc:
        return jsonify({"error": str(exc)}), 400
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 502


@bp.post("/deployments/<int:deployment_id>/tables/<table_name>/columns")
def alter_columns(deployment_id: int, table_name: str):
    """对单表应用一组字段操作：add/drop/rename/change_type；工程室=创建人独有（DR-14）。"""
    _deployment_and_role(current_app, deployment_id)
    _require_creator(deployment_id)
    data = _body()
    ops = data.get("ops")
    if not isinstance(ops, list) or not ops:
        return jsonify({"error": "ops 必须是非空操作列表"}), 400
    try:
        result = editor.alter_table(current_app, deployment_id, table_name, ops)
        log_system(current_app, deployment_id, "structure", actor_user_id=_uid(),
                   object_kind="table", object_table=table_name,
                   object_pk=table_name,
                   object_label=f"{table_name} 结构变更",
                   changes={"ops": _ops_summary(ops)})
        return jsonify(result)
    except editor.EditorError as exc:
        return jsonify({"error": str(exc)}), 400
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 502


# ---------------- 0.11.0 M5-A 结构演化：对自建库增表/删表（DR-27/DR-31） ----------------

@bp.post("/deployments/<int:deployment_id>/tables/propose")
def propose_add_table(deployment_id: int):
    """增表提案：只算不执行（建表 DDL + 待生成页面/菜单计划）。工程室=创建人独有。"""
    _deployment_and_role(current_app, deployment_id)
    _require_creator(deployment_id)
    spec = _body().get("spec")
    if not isinstance(spec, dict):
        return jsonify({"error": "spec 必须是表规格对象"}), 400
    try:
        return jsonify(structure_service.propose_add_table(current_app, deployment_id, spec))
    except StructureError as exc:
        return jsonify({"error": str(exc)}), 400
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 502


@bp.post("/deployments/<int:deployment_id>/tables/apply")
def apply_add_table(deployment_id: int):
    """增表确认执行：建表 + 写元数据（页面/菜单/配置）+ structure 日志。创建人独有。"""
    _deployment_and_role(current_app, deployment_id)
    _require_creator(deployment_id)
    spec = _body().get("spec")
    if not isinstance(spec, dict):
        return jsonify({"error": "spec 必须是表规格对象"}), 400
    try:
        return jsonify(structure_service.add_table(current_app, deployment_id, spec, _uid())), 201
    except StructureError as exc:
        return jsonify({"error": str(exc)}), 400
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 502


@bp.post("/deployments/<int:deployment_id>/tables/<table_name>/drop/propose")
def propose_drop_table(deployment_id: int, table_name: str):
    """删表影响面提案（不执行）：行数/外键引用/页面/菜单/授权/流程/报表/种子 + 阻断判定。"""
    _deployment_and_role(current_app, deployment_id)
    _require_creator(deployment_id)
    try:
        return jsonify(structure_service.analyze_drop_table(current_app, deployment_id, table_name))
    except StructureError as exc:
        return jsonify({"error": str(exc)}), 400
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 502


@bp.post("/deployments/<int:deployment_id>/tables/<table_name>/drop")
def confirm_drop_table(deployment_id: int, table_name: str):
    """删表确认执行：守卫（被引用/被流程引用则拒绝）→ 级联清元数据 → 物理 DROP。创建人独有。

    D-6：服务端二次确认——请求体须带 `{"confirm": "<表名>"}` 且完全一致，否则 400。
    """
    _deployment_and_role(current_app, deployment_id)
    _require_creator(deployment_id)
    confirm = (_body().get("confirm") or "").strip()
    if confirm != table_name:
        return jsonify({"error": "请提供与表名一致的 confirm 以确认删除"}), 400
    try:
        return jsonify(structure_service.drop_table(current_app, deployment_id, table_name, _uid()))
    except StructureError as exc:
        return jsonify({"error": str(exc)}), 400
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 502


def _ops_summary(ops: list[dict]) -> list[dict]:
    """结构变更的人话摘要：{动作, 字段}，给日志页直接展示。"""
    return [
        {"action": op.get("action", ""),
         "name": op.get("name", "") or op.get("new_name", "")}
        for op in ops
    ]


def _require_creator(deployment_id: int) -> None:
    """工程室动作：仅创建人（DR-14，管理员/操作员无）。"""
    Session = current_app.extensions["datamason_session_factory"]
    with Session() as s:
        d = s.get(Deployment, deployment_id)
        if d is None:
            abort(404, description="部署不存在")
        if d.owner_id != _uid() and member_service.get_role(s, d, _uid()) != "creator":
            abort(403, description="仅创建人可进行工程室操作（管理员无此项能力）")
