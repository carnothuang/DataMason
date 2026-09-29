"""导入 / XLSX 导出路由（0.11.0 M5-B3/B5）。

挂 `/api/v1/app`。鉴权：成员 + 动作权限（import/export）+ 数据范围（由 CRUD 引擎强制）。
"""
from __future__ import annotations

import io

from flask import Blueprint, current_app, jsonify, request, send_file, session
from openpyxl import Workbook
from werkzeug.exceptions import Conflict, HTTPException

from datamason.crud import engine as crud_engine
from datamason.crud import runtime as crud_runtime
from datamason.imports import service as imports_service
from datamason.imports.service import ImportError_
from datamason.members import service as member_service
from datamason.members.service import PermissionDenied
from datamason.models import Deployment

bp = Blueprint("imports", __name__)


def _uid() -> int:
    return session.get("user_id") or 1


def _guard(deployment_id: int, table_name: str, action: str) -> None:
    Session = current_app.extensions["datamason_session_factory"]
    with Session() as s:
        d = s.get(Deployment, deployment_id)
        if d is None:
            raise KeyError("部署不存在")
        if d.status != "running":
            # D-3：与工作台一致，停止态禁用导入/导出
            raise Conflict(description="部署已停止，导入/导出不可用；可在部署清单再启动")
        uid = _uid()
        member_service.assert_member(s, d, uid)
        member_service.assert_action(s, d, uid, table_name, action)


@bp.post("/<int:deployment_id>/api/tables/<name>/import/preview")
def import_preview(deployment_id: int, name: str):
    _guard(deployment_id, name, "import")
    try:
        info = imports_service.preview_import(
            current_app, deployment_id, name, request.files.get("file"), _uid())
        return jsonify(info)
    except ImportError_ as exc:
        return jsonify({"error": str(exc)}), 400


@bp.post("/<int:deployment_id>/api/tables/<name>/import/commit")
def import_commit(deployment_id: int, name: str):
    _guard(deployment_id, name, "import")
    data = request.get_json(silent=True) or {}
    try:
        result = imports_service.commit_import(
            current_app, deployment_id, name,
            data.get("upload_token", ""), data.get("mapping") or {},
            data.get("mode", "strict"), _uid())
        return jsonify(result)
    except ImportError_ as exc:
        return jsonify({"error": str(exc)}), 400


@bp.get("/<int:deployment_id>/api/tables/<name>/import/template")
def import_template(deployment_id: int, name: str):
    _guard(deployment_id, name, "import")
    try:
        data = imports_service.build_template(current_app, deployment_id, name)
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404
    resp = current_app.response_class(
        data,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    resp.headers["Content-Disposition"] = f'attachment; filename="{name}-template.xlsx"'
    return resp


@bp.get("/<int:deployment_id>/api/tables/<name>/rows.xlsx")
def export_xlsx(deployment_id: int, name: str):
    """XLSX 导出（与 CSV 同筛选；受 export 动作与数据范围约束）。"""
    _guard(deployment_id, name, "export")
    registry, engine = crud_runtime._registry(current_app, deployment_id, _uid())[1:]
    try:
        columns, rows, truncated = crud_engine.export_rows(
            registry, name, int(current_app.config.get("DM_EXPORT_MAX_ROWS", 20000)))
    finally:
        engine.dispose()
    wb = Workbook()
    ws = wb.active
    ws.title = name[:30]
    ws.append(columns)
    for r in rows:
        ws.append(["" if r.get(c) is None else r.get(c) for c in columns])
    buf = io.BytesIO()
    wb.save(buf)
    resp = current_app.response_class(
        buf.getvalue(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    resp.headers["Content-Disposition"] = f'attachment; filename="{name}.xlsx"'
    resp.headers["X-DM-Truncated"] = "true" if truncated else "false"
    return resp


@bp.errorhandler(PermissionDenied)
def _permission_error(exc: PermissionDenied):
    return jsonify({"error": str(exc)}), 403


@bp.errorhandler(HTTPException)
def _http_error(exc: HTTPException):
    return jsonify({"error": exc.description}), exc.code


@bp.errorhandler(KeyError)
def _key_error(exc: KeyError):
    return jsonify({"error": str(exc)}), 404


@bp.errorhandler(Exception)
def _generic_error(exc: Exception):  # noqa: BLE001
    return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 400
