"""附件文件路由（0.11.0 M5-B1）。

挂 `/api/v1/app`，与工作台同前缀；**不在 API key 白名单**（DR-33：key 不开文件接口）。
鉴权：全局 guard 要求登录 → 成员校验；操作员受数据范围约束（挂行附件按行可见性判定）。
"""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request, send_file, session
from werkzeug.exceptions import Conflict, HTTPException

from datamason.crud import engine as crud_engine
from datamason.crud import runtime as crud_runtime
from datamason.files import service as files_service
from datamason.files.service import FileError
from datamason.members import service as member_service
from datamason.members.service import PermissionDenied

bp = Blueprint("files", __name__)


def _uid() -> int:
    return session.get("user_id") or 1


def _deployment_id() -> int:
    return int(request.view_args["deployment_id"])


def _assert_access(attachment) -> str:
    """成员校验 + 操作员按行可见性；返回角色。"""
    deployment_id = _deployment_id()
    _assert_running(deployment_id)
    Session = current_app.extensions["datamason_session_factory"]
    with Session() as s:
        from datamason.models import Deployment

        d = s.get(Deployment, deployment_id)
        if d is None:
            raise KeyError("部署不存在")
        uid = _uid()
        role = member_service.assert_member(s, d, uid)
    if role != "operator":
        return role
    if attachment.table_name and attachment.row_pk:
        _d, registry, engine = crud_runtime._registry(current_app, deployment_id, uid)
        try:
            crud_engine.get_row(registry, attachment.table_name, attachment.row_pk)
        except HTTPException as exc:
            if exc.code == 404:
                raise PermissionDenied("无权访问该记录的附件") from exc
            raise
        finally:
            engine.dispose()
    return role


def _assert_running(deployment_id: int) -> None:
    """D-3：停止态部署的文件写入/读取与工作台一致禁用（409）。"""
    from datamason.models import Deployment

    Session = current_app.extensions["datamason_session_factory"]
    with Session() as s:
        d = s.get(Deployment, deployment_id)
        if d is None:
            raise KeyError("部署不存在")
        if d.status != "running":
            raise Conflict(description="部署已停止，文件/导入操作不可用；可在部署清单再启动")


def _assert_member() -> str:
    deployment_id = _deployment_id()
    _assert_running(deployment_id)
    Session = current_app.extensions["datamason_session_factory"]
    with Session() as s:
        from datamason.models import Deployment

        d = s.get(Deployment, deployment_id)
        if d is None:
            raise KeyError("部署不存在")
        return member_service.assert_member(s, d, _uid())


@bp.post("/<int:deployment_id>/api/files")
def upload_file(deployment_id: int):
    # 成员校验（不限定动作；具体写权限由所属行 CRUD 保证）
    _assert_member()
    f = request.files.get("file")
    try:
        info = files_service.save_upload(
            current_app, deployment_id, f,
            table_name=(request.form.get("table") or ""),
            row_pk=(request.form.get("row_pk") or ""),
            field_name=(request.form.get("field") or ""),
            uploaded_by=_uid(),
        )
        return jsonify(info), 201
    except FileError as exc:
        return jsonify({"error": str(exc)}), 400


@bp.get("/<int:deployment_id>/api/files/<token>")
def download_file(deployment_id: int, token: str):
    a = files_service.get_attachment(current_app, deployment_id, token)
    if a is None:
        return jsonify({"error": "附件不存在"}), 404
    _assert_access(a)
    path = files_service.abs_path(current_app, a)
    if not path.exists():
        return jsonify({"error": "文件已丢失（可能未随数据一起迁移）"}), 404
    try:
        return send_file(path, mimetype=a.mime or "application/octet-stream",
                         as_attachment=True, download_name=a.filename or a.token)
    except (FileNotFoundError, OSError):
        return jsonify({"error": "文件已丢失（可能未随数据一起迁移）"}), 404


@bp.get("/<int:deployment_id>/api/files/<token>/preview")
def preview_file(deployment_id: int, token: str):
    a = files_service.get_attachment(current_app, deployment_id, token)
    if a is None:
        return jsonify({"error": "附件不存在"}), 404
    _assert_access(a)
    if not files_service.is_inline(a.mime):
        return jsonify({"error": "该类型不支持内嵌预览，请下载查看"}), 415
    path = files_service.abs_path(current_app, a)
    if not path.exists():
        return jsonify({"error": "文件已丢失（可能未随数据一起迁移）"}), 404
    try:
        resp = send_file(path, mimetype=a.mime or "application/octet-stream",
                         as_attachment=False, download_name=a.filename or a.token)
    except (FileNotFoundError, OSError):
        return jsonify({"error": "文件已丢失（可能未随数据一起迁移）"}), 404
    resp.headers["X-Content-Type-Options"] = "nosniff"
    # D-2：即使扩展名/类型判定失误，也用 CSP sandbox 阻断脚本与同源能力
    resp.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return resp


@bp.delete("/<int:deployment_id>/api/files/<token>")
def delete_file(deployment_id: int, token: str):
    a = files_service.get_attachment(current_app, deployment_id, token)
    if a is None:
        return jsonify({"error": "附件不存在"}), 404
    _assert_access(a)
    files_service.remove_attachment(current_app, deployment_id, token)
    return jsonify({"deleted": True})


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
