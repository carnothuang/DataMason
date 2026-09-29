"""表单控件配置路由（0.13.0 M7-3/M7-7）。

薄路由：解析 → 取会话用户 → 权限校验 → 调服务（`current_app` 为首参）→ jsonify。
- 配置管理（读/存/恢复默认/NL 生成候选项）：部署创建人或平台管理员（M7-3-4）；
- 运行态只读配置（供表单渲染）：任意成员（操作员仅可读其被授权表）。
"""
from __future__ import annotations

from flask import Blueprint, abort, current_app, jsonify, request, session
from werkzeug.exceptions import HTTPException

from datamason.forms import service
from datamason.forms.service import FormConfigError
from datamason.llm.client import LlmCallError
from datamason.members import service as member_service
from datamason.members.service import PermissionDenied
from datamason.models import Deployment, TableSpec, User

bp = Blueprint("forms", __name__)

WORKSPACE_KIND = "workspace"


def _uid() -> int:
    return session.get("user_id") or 1


def _body() -> dict:
    return request.get_json(silent=True) or {}


@bp.errorhandler(FormConfigError)
def _config_error(exc: FormConfigError):
    return jsonify({"error": str(exc)}), 400


@bp.errorhandler(PermissionDenied)
def _permission_error(exc: PermissionDenied):
    return jsonify({"error": str(exc)}), 403


@bp.errorhandler(LlmCallError)
def _llm_error(exc: LlmCallError):
    return jsonify({"error": str(exc)}), 502


@bp.errorhandler(HTTPException)
def _http_error(exc: HTTPException):
    return jsonify({"error": exc.description}), exc.code


@bp.errorhandler(KeyError)
def _key_error(exc: KeyError):
    return jsonify({"error": str(exc)}), 404


def _require_form_config(deployment_id: int) -> Deployment:
    """配置读写权限：部署 owner/creator 或平台管理员（需求 §4.3.1）。"""
    uid = _uid()
    Session = current_app.extensions["datamason_session_factory"]
    with Session() as s:
        d = s.get(Deployment, deployment_id)
        if d is None:
            abort(404, description="部署不存在")
        role = member_service.get_role(s, d, uid)
        user = s.get(User, uid)
        if role != "creator" and not (user and user.is_platform_admin):
            abort(403, description="你没有调整表单配置的权限（仅创建人与平台管理员）")
        return d


def _require_member_read(deployment_id: int, table_name: str) -> None:
    """运行态读配置：任意成员；操作员仅可读其被授权表（越权 403）。"""
    uid = _uid()
    Session = current_app.extensions["datamason_session_factory"]
    with Session() as s:
        d = s.get(Deployment, deployment_id)
        if d is None:
            abort(404, description="部署不存在")
        role = member_service.assert_member(s, d, uid)
        if role == "operator":
            access = member_service.table_access_map(s, d, uid)
            if table_name not in access:
                abort(403, description="你没有该数据表的权限")


@bp.get("/deployments/<int:deployment_id>/forms/<table>")
def get_config(deployment_id: int, table: str):
    _require_form_config(deployment_id)
    return jsonify(service.get_form_config(
        current_app, deployment_id, table, page_kind=WORKSPACE_KIND))


@bp.put("/deployments/<int:deployment_id>/forms/<table>")
def save_config(deployment_id: int, table: str):
    _require_form_config(deployment_id)
    return jsonify(service.save_form_config(
        current_app, deployment_id, table, _body(),
        page_kind=WORKSPACE_KIND, actor_user_id=_uid()))


@bp.delete("/deployments/<int:deployment_id>/forms/<table>/fields/<field>")
def reset_field(deployment_id: int, table: str, field: str):
    _require_form_config(deployment_id)
    return jsonify(service.reset_field_config(
        current_app, deployment_id, table, field, actor_user_id=_uid()))


@bp.get("/app/<int:deployment_id>/api/forms/<table>")
def runtime_config(deployment_id: int, table: str):
    """运行态只读配置（任意成员）：供工作台表单按配置渲染。"""
    _require_member_read(deployment_id, table)
    return jsonify(service.get_form_config(
        current_app, deployment_id, table, page_kind=WORKSPACE_KIND))


@bp.post("/deployments/<int:deployment_id>/forms/<table>/fields/<field>/options/generate")
def generate_options(deployment_id: int, table: str, field: str):
    """NL 生成候选项候选清单（配置者，可选能力）：只返回清单，不写配置。"""
    _require_form_config(deployment_id)
    data = _body()
    description = (data.get("description") or "").strip()
    if not description:
        raise FormConfigError("请描述候选项的取值来源")
    Session = current_app.extensions["datamason_session_factory"]
    with Session() as s:
        ts = (
            s.query(TableSpec)
            .filter_by(deployment_id=deployment_id, name=table)
            .first()
        )
        col = None
        if ts is not None:
            col = next((c for c in ts.columns if c.get("name") == field), None)
    if col is None:
        raise FormConfigError(f"字段不存在：{field}")
    label = col.get("biz_name") or field
    return jsonify(service.generate_option_items(
        current_app, field_label=label, description=description,
        deployment_id=deployment_id))
