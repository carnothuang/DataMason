"""附件与文件服务（0.11.0 M5-B1，DR-28/DR-33）。

- 文件落每部署目录 `DATA_ROOT/deployments/deployment_<id>.files/`，二进制不进业务库。
- 业务表 FILE 列只存 attachment id（或 id 数组），元数据落 `attachments` 表。
- 安全：随机 token 命名、扩展名白名单、单文件与请求上限、路径穿越防护。
"""
from __future__ import annotations

import mimetypes
import secrets
from pathlib import Path

from server.models import Attachment, Deployment, User

# 可内嵌预览的类型前缀（其余一律 attachment 下载）
INLINE_MIME_PREFIXES = ("image/", "text/")
INLINE_MIME_EXACT = ("application/pdf",)


class FileError(RuntimeError):
    pass


def _session(app):
    return app.extensions["datamason_session_factory"]()


def _files_dir(app, deployment_id: int) -> Path:
    root = Path(app.config.get("DATA_ROOT", "./data")).resolve()
    d = root / "deployments" / f"deployment_{deployment_id}.files"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _allowed_exts(app) -> set[str]:
    raw = app.config.get("DM_UPLOAD_ALLOWED_EXT", "") or ""
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def _within_data_root(app, path: Path) -> bool:
    root = Path(app.config.get("DATA_ROOT", "./data")).resolve()
    try:
        resolved = path.resolve()
    except OSError:
        return False
    return resolved == root or root in resolved.parents


def save_upload(app, deployment_id: int, file_storage, *,
                table_name: str = "", row_pk: str = "", field_name: str = "",
                uploaded_by: int = 0) -> dict:
    """保存上传文件并登记 attachments；返回附件公开信息。"""
    if file_storage is None or not getattr(file_storage, "filename", ""):
        raise FileError("未收到文件")
    filename = file_storage.filename
    ext = Path(filename).suffix.lower()
    allowed = _allowed_exts(app)
    if allowed and ext not in allowed:
        raise FileError(f"不支持的文件类型：{ext or '（无扩展名）'}")

    # 读取到内存并做大小校验（<= 单文件上限）
    data = file_storage.read()
    max_bytes = int(app.config.get("DM_UPLOAD_MAX_FILE_BYTES", 10 * 1024 * 1024))
    if len(data) > max_bytes:
        raise FileError(f"文件超过上限（{max_bytes // (1024 * 1024)}MB）")

    token = secrets.token_urlsafe(24).replace("-", "").replace("_", "")[:32]
    rel = f"deployments/deployment_{deployment_id}.files/{token}{ext}"
    abs_path = Path(app.config.get("DATA_ROOT", "./data")) / rel
    if not _within_data_root(app, abs_path):
        raise FileError("文件路径非法")
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(data)

    mime = file_storage.mimetype or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    with _session(app) as s:
        a = Attachment(
            deployment_id=deployment_id, table_name=table_name, row_pk=str(row_pk or ""),
            field_name=field_name, token=token, filename=filename, mime=mime,
            size=len(data), storage_rel=rel, status="active", uploaded_by=uploaded_by,
        )
        s.add(a)
        s.commit()
        return _public(a)


def _public(a: Attachment) -> dict:
    return {
        "id": a.id, "token": a.token, "filename": a.filename, "mime": a.mime,
        "size": a.size, "table_name": a.table_name, "row_pk": a.row_pk,
        "field_name": a.field_name, "status": a.status,
    }


def get_attachment(app, deployment_id: int, token: str) -> Attachment | None:
    with _session(app) as s:
        return (
            s.query(Attachment)
            .filter_by(deployment_id=deployment_id, token=token)
            .first()
        )


def abs_path(app, attachment: Attachment) -> Path:
    p = Path(app.config.get("DATA_ROOT", "./data")) / (attachment.storage_rel or "")
    if not _within_data_root(app, p):
        raise FileError("文件路径非法")
    return p


def is_inline(mime: str) -> bool:
    m = (mime or "").lower()
    return m in INLINE_MIME_EXACT or m.startswith(INLINE_MIME_PREFIXES)


def remove_attachment(app, deployment_id: int, token: str) -> bool:
    """删除附件记录与磁盘文件；幂等。"""
    with _session(app) as s:
        a = (
            s.query(Attachment)
            .filter_by(deployment_id=deployment_id, token=token)
            .first()
        )
        if a is None:
            return False
        p = abs_path(app, a)
        s.delete(a)
        s.commit()
    try:
        if p.exists():
            p.unlink()
    except OSError:
        pass
    return True


def mark_orphans_pending(app, deployment_id: int, table_name: str, row_pks: list[str]) -> int:
    """行删除后把其附件标记 pending_gc（幂等回收任务再删文件）。"""
    if not row_pks:
        return 0
    with _session(app) as s:
        rows = (
            s.query(Attachment)
            .filter_by(deployment_id=deployment_id, table_name=table_name)
            .filter(Attachment.row_pk.in_([str(p) for p in row_pks]))
            .all()
        )
        n = 0
        for a in rows:
            if a.status != "pending_gc":
                a.status = "pending_gc"
                n += 1
        s.commit()
        return n


def cleanup_orphans(app, deployment_id: int | None = None) -> int:
    """回收 pending_gc 附件（启动/后台调用）；幂等可重入。"""
    with _session(app) as s:
        q = s.query(Attachment).filter_by(status="pending_gc")
        if deployment_id is not None:
            q = q.filter_by(deployment_id=deployment_id)
        rows = q.all()
        tokens = [(r.deployment_id, r.token, r.storage_rel) for r in rows]
    removed = 0
    for _did, token, _rel in tokens:
        if remove_attachment(app, _did, token):
            removed += 1
    return removed


def resolve_deployment(app, deployment_id: int) -> Deployment | None:
    with _session(app) as s:
        return s.get(Deployment, deployment_id)


def user_display(app, user_id: int) -> str:
    with _session(app) as s:
        u = s.get(User, user_id)
        return (u.display_name or u.username) if u else str(user_id)
