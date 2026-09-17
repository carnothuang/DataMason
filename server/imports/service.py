"""CSV/XLSX 导入（0.11.0 M5-B3，DR-29/DR-32）。

四步向导的服務端：上传解析 → 列映射 → 校验预览 → 确认导入。
- 提案式：先返回映射、样例行与错误清单，确认后才写入，永不盲写。
- 日志：整批聚合为 1 条 `action=import`（DR-29），不逐行写。
"""
from __future__ import annotations

import csv
import io
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

from openpyxl import Workbook, load_workbook
from sqlalchemy import func, select

from server.audit.service import log_system
from server.crud import engine as crud_engine
from server.crud import runtime as crud_runtime
from server.members import service as member_service
from server.models import TableSpec


class ImportError_(RuntimeError):
    pass


def _session(app):
    return app.extensions["datamason_session_factory"]()


def _tmp_dir(app) -> Path:
    d = Path(app.config.get("DATA_ROOT", "./data")) / "tmp" / "import"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _within_data_root(app, path: Path) -> bool:
    root = Path(app.config.get("DATA_ROOT", "./data")).resolve()
    try:
        resolved = path.resolve()
    except OSError:
        return False
    return resolved == root or root in resolved.parents


def _read_rows(filename: str, data: bytes) -> tuple[list[str], list[list]]:
    ext = Path(filename).suffix.lower()
    if ext in (".xlsx", ".xlsm"):
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        ws = wb.active
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        wb.close()
    elif ext in (".csv", ".txt"):
        text = data.decode("utf-8-sig", errors="replace")
        rows = [list(r) for r in csv.reader(io.StringIO(text))]
    else:
        raise ImportError_("仅支持 CSV / XLSX 文件")
    if not rows:
        raise ImportError_("文件为空")
    headers = [str(h).strip() if h is not None else "" for h in rows[0]]
    body = [r for r in rows[1:] if any(c is not None and str(c).strip() != "" for c in r)]
    return headers, body


def _table_spec(app, deployment_id: int, table_name: str) -> TableSpec:
    with _session(app) as s:
        ts = (
            s.query(TableSpec)
            .filter_by(deployment_id=deployment_id, name=table_name)
            .first()
        )
        if ts is None:
            raise KeyError(f"表 {table_name} 不存在")
        return ts


def _auto_mapping(headers: list[str], columns: list[dict]) -> dict:
    norm = {h.strip().lower(): i for i, h in enumerate(headers)}
    mapping: dict[str, int] = {}
    for c in columns:
        if c.get("is_ownership") or c.get("type") == "FILE":
            continue
        for key in (c["name"], c.get("biz_name", "")):
            k = (key or "").strip().lower()
            if k and k in norm:
                mapping[c["name"]] = norm[k]
                break
    return mapping


def _cell(row: list, idx: int):
    if idx is None or idx >= len(row):
        return None
    v = row[idx]
    if v is None:
        return None
    if isinstance(v, str):
        v = v.strip()
        return v if v != "" else None
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    return v


def _coerce(col: dict, raw):
    t = (col.get("type") or "VARCHAR").upper()
    if raw is None:
        return None
    if t in ("INTEGER", "BIGINT"):
        return int(float(str(raw)))
    if t == "DECIMAL":
        return float(raw)
    if t == "BOOLEAN":
        return str(raw).lower() in ("1", "true", "yes", "y", "是", "真")
    if t == "DATETIME":
        txt = str(raw).replace("T", " ")
        datetime.fromisoformat(txt)
        return txt
    return str(raw)


def _validate(app, deployment_id: int, table_name: str, columns: list[dict],
              mapping: dict, body: list[list], registry) -> list[dict]:
    """返回错误清单 [{row, column, message}]（行号从 2 起，对应文件第 2 行）。"""
    errors: list[dict] = []
    pk_col = next((c for c in columns if c.get("is_pk")), None)
    check_cols = [c for c in columns if c["name"] in mapping]
    # 文件内重复（唯一列）
    seen: dict[str, set] = {}
    for row_idx, row in enumerate(body, start=2):
        for c in check_cols:
            name = c["name"]
            raw = _cell(row, mapping.get(name))
            if raw is None:
                if not c.get("nullable", True):
                    errors.append({"row": row_idx, "column": name, "message": "必填为空"})
                continue
            # 类型
            try:
                value = _coerce(c, raw)
            except (ValueError, TypeError):
                errors.append({"row": row_idx, "column": name,
                               "message": f"类型应为 {c.get('type')}"})
                continue
            # 长度
            length = c.get("length")
            if c.get("type") == "VARCHAR" and length and len(str(value)) > int(length):
                errors.append({"row": row_idx, "column": name, "message": f"超过长度 {length}"})
            # 文件内重复
            if c.get("unique"):
                key = str(value)
                bucket = seen.setdefault(name, set())
                if key in bucket:
                    errors.append({"row": row_idx, "column": name, "message": "文件内重复"})
                bucket.add(key)
            # 库内唯一
            if c.get("unique") and registry is not None:
                table = registry.table(table_name)
                if name in table.c:
                    with registry.engine.connect() as conn:
                        n = conn.execute(
                            select(func.count()).select_from(table)
                            .where(table.c[name] == value)
                        ).scalar_one()
                    if n:
                        errors.append({"row": row_idx, "column": name,
                                       "message": "与库中已有值重复"})
            # 外键存在性
            if c.get("is_fk") and c.get("ref_table") and registry is not None:
                ref = registry.meta.tables.get(c["ref_table"])
                if ref is not None:
                    ref_col = c.get("ref_column") or "id"
                    with registry.engine.connect() as conn:
                        n = conn.execute(
                            select(func.count()).select_from(ref)
                            .where(ref.c[ref_col] == value)
                        ).scalar_one()
                    if not n:
                        errors.append({"row": row_idx, "column": name,
                                       "message": f"关联的 {c['ref_table']} 不存在"})
    if pk_col is None:
        errors.append({"row": 0, "column": "", "message": "该表没有主键，暂不支持导入"})
    return errors


def _required_columns(columns: list[dict]) -> list[str]:
    """必填列（非归属/非 FILE/非主键且不可空）。"""
    return [
        c["name"] for c in columns
        if not c.get("is_ownership") and not c.get("is_pk")
        and c.get("type") != "FILE" and not c.get("nullable", True)
    ]


def _cleanup_tmp(app) -> None:
    """D-5：清理过期的导入临时文件（TTL 可配，默认 1 小时）。"""
    import time

    ttl = int(app.config.get("DM_IMPORT_TMP_TTL_SECONDS", 3600))
    now = time.time()
    try:
        for p in _tmp_dir(app).glob("*"):
            if p.is_file() and now - p.stat().st_mtime > ttl:
                p.unlink(missing_ok=True)
    except OSError:
        pass


def preview_import(app, deployment_id: int, table_name: str, file_storage,
                   uid: int) -> dict:
    if file_storage is None or not getattr(file_storage, "filename", ""):
        raise ImportError_("未收到文件")
    data = file_storage.read()
    max_rows = int(app.config.get("DM_IMPORT_MAX_ROWS", 5000))
    headers, body = _read_rows(file_storage.filename, data)
    if len(body) > max_rows:
        raise ImportError_(f"行数超过上限（{max_rows}），请分批导入")
    if not body:
        raise ImportError_("没有可导入的数据行")

    ts = _table_spec(app, deployment_id, table_name)
    columns = ts.columns
    mapping = _auto_mapping(headers, columns)

    # D-4：必填列未映射 → 预览阶段直接阻断（提案式：不让人看到全绿后才失败）
    missing_required = [name for name in _required_columns(columns) if name not in mapping]

    _cleanup_tmp(app)
    # 落临时文件，commit 时按 token 复用
    token = secrets.token_urlsafe(16).replace("-", "").replace("_", "")[:24]
    ext = Path(file_storage.filename).suffix.lower()
    p = _tmp_dir(app) / f"{token}{ext}"
    if not _within_data_root(app, p):
        raise ImportError_("路径非法")
    p.write_bytes(data)

    _d, registry, engine = crud_runtime._registry(app, deployment_id, uid)
    try:
        errors = _validate(app, deployment_id, table_name, columns, mapping, body, registry)
    finally:
        engine.dispose()
    if missing_required:
        errors = [
            {"row": 0, "column": name, "message": "必填列未映射（请在列映射中指定）"}
            for name in missing_required
        ] + errors

    sample = [
        {c: _cell(row, mapping.get(c)) for c in mapping}
        for row in body[:5]
    ]
    error_rows = {e["row"] for e in errors}
    return {
        "upload_token": token,
        "headers": headers,
        "mapping": mapping,
        "columns": [
            {"name": c["name"], "biz_name": c.get("biz_name") or c["name"],
             "type": c.get("type"), "nullable": c.get("nullable", True),
             "unique": bool(c.get("unique")), "is_fk": bool(c.get("is_fk")),
             "is_ownership": bool(c.get("is_ownership")), "is_pk": bool(c.get("is_pk"))}
            for c in columns
        ],
        "sample": sample,
        "total": len(body),
        "valid": 0 if missing_required else len(body) - len(error_rows),
        "error_count": len(errors),
        "blocking": missing_required,
        "can_commit": not missing_required,
        "errors": errors[:200],
    }


def commit_import(app, deployment_id: int, table_name: str, upload_token: str,
                  mapping: dict, mode: str, uid: int) -> dict:
    if mode not in ("strict", "skip_errors"):
        raise ImportError_("mode 仅支持 strict / skip_errors")
    if not isinstance(mapping, dict) or not mapping:
        raise ImportError_("缺少列映射")
    # 定位临时文件
    tmp = _tmp_dir(app)
    matches = list(tmp.glob(f"{upload_token}.*"))
    if not matches:
        raise ImportError_("上传文件已过期，请重新上传")
    p = matches[0]
    if not _within_data_root(app, p):
        raise ImportError_("路径非法")
    headers, body = _read_rows(p.name, p.read_bytes())
    ts = _table_spec(app, deployment_id, table_name)
    columns = ts.columns
    mapping = {k: int(v) for k, v in mapping.items() if v is not None and str(v) != ""}
    missing_required = [name for name in _required_columns(columns) if name not in mapping]
    if missing_required:
        raise ImportError_("必填列未映射：" + "、".join(missing_required))

    _d, registry, engine = crud_runtime._registry(app, deployment_id, uid)
    inserted = 0
    try:
        table = registry.table(table_name)
        pk_col = next((c for c in columns if c.get("is_pk")), None)
        target_cols = [c for c in columns if c["name"] in mapping and c["name"] in table.c
                       and not c.get("is_ownership") and not (pk_col and c["name"] == pk_col["name"])]
        now = datetime.now(timezone.utc)
        rows_values = []
        errors = _validate(app, deployment_id, table_name, columns, mapping, body, registry)
        bad_rows = {e["row"] for e in errors}

        with engine.begin() as conn:
            for row_idx, row in enumerate(body, start=2):
                if mode == "strict" and row_idx in bad_rows:
                    raise ImportError_(f"第 {row_idx} 行校验未通过，已整体回滚（可改用「跳过错误行」）")
                if row_idx in bad_rows:
                    continue
                values = {}
                for c in target_cols:
                    values[c["name"]] = _coerce(c, _cell(row, mapping.get(c["name"])))
                if "created_by" in table.c:
                    values["created_by"] = uid
                if "created_at" in table.c:
                    values["created_at"] = now
                if "updated_by" in table.c:
                    values["updated_by"] = uid
                if "updated_at" in table.c:
                    values["updated_at"] = now
                conn.execute(table.insert().values(**values))
                inserted += 1
    except ImportError_:
        engine.dispose()
        raise
    except Exception as exc:  # noqa: BLE001
        engine.dispose()
        raise ImportError_(f"导入失败：{type(exc).__name__}: {exc}") from exc
    engine.dispose()

    skipped = len(body) - inserted
    log_system(
        app, deployment_id, "import", actor_user_id=uid,
        object_kind="table", object_table=table_name, object_pk="",
        object_label=f"导入 {table_name}：成功 {inserted} 行，跳过 {skipped} 行",
        changes={"file": p.name, "inserted": inserted, "skipped": skipped, "mode": mode},
    )
    try:
        p.unlink()
    except OSError:
        pass
    return {"inserted": inserted, "skipped": skipped, "total": len(body)}


def build_template(app, deployment_id: int, table_name: str) -> bytes:
    ts = _table_spec(app, deployment_id, table_name)
    cols = [c for c in ts.columns
            if not c.get("is_ownership") and not c.get("is_pk") and c.get("type") != "FILE"]
    wb = Workbook()
    ws = wb.active
    ws.title = table_name[:30]
    ws.append([c.get("biz_name") or c["name"] for c in cols])
    ws.append([("" if c.get("nullable", True) else "必填") for c in cols])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
