"""部署路由：部署清单 + 部署流（预览提案 / 确认部署）+ 数据浏览/蓝图（M2-5 接成员权限）。"""
from __future__ import annotations

from flask import Blueprint, abort, current_app, jsonify, request, session
from werkzeug.exceptions import HTTPException

from server.crud import runtime
from server.audit.service import log_system
from server.deploy import link, service
from server.deploy.service import DeployError
from server.deploy.link import LinkError
from server.deploy import editor
from server.members import service as member_service
from server.members.service import PermissionDenied
from server.models import Deployment

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


@bp.get("/deployments")
def list_deployments():
    return jsonify(service.list_deployments(current_app))


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
        )
        return jsonify(result), 201
    except service.DeployError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 502


# ---------------- M2-7 直连模式（接已有的，DR-09/DR-18） ----------------

@bp.get("/deployments/link/connections")
def link_connections():
    """可直连的连接清单：登录即可见（区别于 /settings 连接管理需平台管理员）。"""
    from server.settings.service import list_connections

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

_RESERVED = {"page", "page_size", "sort", "order"}


@bp.get("/deployments/<int:deployment_id>/tables")
def deployment_tables(deployment_id: int):
    _deployment_and_role(current_app, deployment_id)  # 成员校验（M2-5）
    try:
        return jsonify(runtime.list_tables(current_app, deployment_id, _uid()))
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404


@bp.get("/deployments/<int:deployment_id>/tables/<table_name>/rows")
def deployment_rows(deployment_id: int, table_name: str):
    _deployment_and_role(current_app, deployment_id)  # 成员校验（M2-5）
    page = max(int(request.args.get("page", 1)), 1)
    page_size = min(max(int(request.args.get("page_size", 20)), 1), 200)
    sort = request.args.get("sort")
    order = request.args.get("order", "asc")
    filters = {k: v for k, v in request.args.items() if k not in _RESERVED}
    try:
        return jsonify(runtime.list_rows(
            current_app, deployment_id, table_name,
            page, page_size, sort, order, filters, _uid(),
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
