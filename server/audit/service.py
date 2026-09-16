"""操作日志（M2-6）：两阶段写入 + 启动对账（DR-03）。

业务写在部署的独立物理库、日志在元数据库——两库无共享事务，故不做跨库
原子性承诺。固定顺序：①元数据库写 pending → ②业务写提交 → ③日志置 committed。
②失败删除①（业务报错、无日志）；③因进程崩溃缺席时由启动对账任务收尾：
pending 超时后回查业务实际生效性（新增/删除查主键存在性，修改查目标值），
生效则 finalize，未生效则丢弃。留痕可靠性=对账保证（非事务保证）。
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Iterable

from server.crud import engine as crud_engine
from server.models import ActivityLog, Deployment, User

# 已实现的系统动作（destroy 随 M3-7 销毁端点接入；flow 随 M3-4；apikey 随 M3-8）
SYSTEM_ACTIONS = ("deploy", "member", "structure", "stop", "start", "flow", "apikey")

_OWNERSHIP_COLS = crud_engine._OWNERSHIP_COLS


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _store_now() -> datetime:
    """日志时间戳：服务器本地（naive）秒级时间。

    前端把 'YYYY-MM-DD HH:MM:SS' 当作本地时间渲染（'今天 14:32' 对齐设计 4.7）；
    单机 MVP 下服务端本地 = 浏览器本地，无需时区换算。
    """
    return datetime.now().replace(microsecond=0)


def jsonable(value):
    """可入 JSON 的展示值：datetime → 'YYYY-MM-DD HH:MM:SS[.ffffff]'。"""
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    return value


def same(a, b) -> bool:
    """宽松相等：布尔 vs 0/1、数值跨类型、datetime string 化后比较。"""
    if a is None or b is None:
        return a is b
    ja, jb = jsonable(a), jsonable(b)
    if isinstance(ja, bool) or isinstance(jb, bool):
        return bool(ja) == bool(jb)
    if isinstance(ja, (int, float)) and isinstance(jb, (int, float)):
        return float(ja) == float(jb)
    return ja == jb


class AuditSession:
    """一次业务写请求的两阶段日志会话（DR-03）。

    生命周期：
      begin() → pending 落库（动态获取 log_id）
      业务提交成功 → commit(log_id[, pk/label/changes]) → committed
      （历史：② 完成后 ③ 前崩溃 → pending 交由启动对账收尾）
      业务提交失败 → abort(log_id) → 删除 pending（业务报错、无日志）
    """

    def __init__(self, app, deployment_id: int, actor_user_id: int | None,
                 source_ip: str = "", actor_label: str = ""):
        self.Session = app.extensions["datamason_session_factory"]
        self.deployment_id = deployment_id
        self.actor_user_id = actor_user_id
        self.source_ip = source_ip
        self.actor_label = actor_label

    def begin(self, action: str, object_table: str, *,
              object_pk: str = "", object_label: str = "", changes: dict | None = None) -> int:
        with self.Session() as s:
            row = ActivityLog(
                deployment_id=self.deployment_id,
                occurred_at=_store_now(),
                actor_user_id=self.actor_user_id,
                actor_label=self.actor_label,
                action=action,
                status="pending",
                object_kind="table",
                object_table=object_table,
                object_pk=object_pk,
                object_label=object_label,
                changes_json=json.dumps(changes or {}, ensure_ascii=False, default=jsonable),
                source_ip=self.source_ip,
            )
            s.add(row)
            s.commit()
            return row.id

    def commit(self, log_id: int, *, object_pk: str | None = None,
               object_label: str | None = None, changes: dict | None = None) -> None:
        with self.Session() as s:
            row = s.get(ActivityLog, log_id)
            if row is None:
                return
            row.status = "committed"
            if object_pk is not None:
                row.object_pk = object_pk
            if object_label is not None:
                row.object_label = object_label
            if changes is not None:
                row.changes_json = json.dumps(changes, ensure_ascii=False, default=jsonable)
            s.commit()

    def abort(self, log_id: int) -> None:
        with self.Session() as s:
            row = s.get(ActivityLog, log_id)
            if row is not None:
                s.delete(row)
                s.commit()


def log_system(app, deployment_id: int, action: str, *, actor_user_id: int | None = None,
               object_kind: str = "", object_table: str = "", object_pk: str = "",
               object_label: str = "", changes: dict | None = None,
               actor_label: str = "") -> int:
    """系统动作单事务写入（同元数据库，天然原子）：deploy/member/structure。

    actor_user_id=None 表示系统（如后续定时任务），通常传触发用户。
    actor_label：非会话身份的显示名（如 API key 名），优先于 actor_user_id 显示。
    """
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        row = ActivityLog(
            deployment_id=deployment_id,
            occurred_at=_store_now(),
            actor_user_id=actor_user_id,
            actor_label=actor_label,
            action=action,
            status="committed",
            object_kind=object_kind,
            object_table=object_table,
            object_pk=object_pk,
            object_label=object_label,
            changes_json=json.dumps(changes or {}, ensure_ascii=False, default=jsonable),
        )
        s.add(row)
        s.commit()
        return row.id


# ---------------- 查询一侧（只追加只读，接口层无 update/delete） ----------------

def _parse_dt(text: str | None) -> datetime | None:
    if not text:
        return None
    clean = text.strip().replace("T", " ")
    try:
        return datetime.fromisoformat(clean)
    except ValueError:
        return None


def list_logs(app, deployment_id: int, role: str, uid: int, *,
              actor_user_id: int | None = None, action: str | None = None,
              object_table: str | None = None, object_pk: str | None = None,
              time_from: str | None = None, time_to: str | None = None,
              page: int = 1, page_size: int = 20) -> dict:
    """倒序分页 + 时间/人/动作/业务对象四维筛选。

    权限（设计细化 §十一 五）：creator/admin 全部可见；operator 强制只含自己的动作。
    pending 不进入查询结果（对账前不展示，风险表 §十一 九）。
    """
    page = max(int(page), 1)
    page_size = min(max(int(page_size), 1), 200)
    Session = app.extensions["datamason_session_factory"]

    with Session() as s:
        frm = _parse_dt(time_from)
        to = _parse_dt(time_to)
        query = s.query(ActivityLog).filter(
            ActivityLog.deployment_id == deployment_id,
            ActivityLog.status == "committed",
        )
        if role == "operator":
            actor_user_id = uid  # 操作员强制只见自己
        if actor_user_id:
            query = query.filter(ActivityLog.actor_user_id == actor_user_id)
        if action:
            query = query.filter(ActivityLog.action == action)
        if object_table:
            query = query.filter(ActivityLog.object_table == object_table)
        if object_pk:
            query = query.filter(ActivityLog.object_pk == object_pk)
        if frm:
            query = query.filter(ActivityLog.occurred_at >= frm)
        if to:
            query = query.filter(ActivityLog.occurred_at < to)

        total = query.count()
        rows = (
            query.order_by(ActivityLog.occurred_at.desc(), ActivityLog.id.desc())
            .offset((page - 1) * page_size).limit(page_size).all()
        )
        users = {u.id: u for u in s.query(User).all()}

    def _display(row: ActivityLog) -> dict:
        user = users.get(row.actor_user_id)
        return {
            "id": row.id,
            "occurred_at": jsonable(row.occurred_at),
            "actor_user_id": row.actor_user_id,
            "actor_display": (
                row.actor_label
                or ((user.display_name or user.username) if user else
                    ("系统" if row.actor_user_id is None else f"#{row.actor_user_id}"))
            ),
            "action": row.action,
            "object_kind": row.object_kind,
            "object_table": row.object_table,
            "object_pk": row.object_pk,
            "object_label": row.object_label,
            "changes": json.loads(row.changes_json or "{}"),
        }

    return {
        "logs": [_display(r) for r in rows],
        "page": page,
        "page_size": page_size,
        "total": total,
    }


# ---------------- 启动对账（DR-03：pending 超时回查生效性） ----------------

def _changes_new(row: ActivityLog) -> dict:
    out = {}
    for field, pair in (row.changes or {}).items():
        if field in _OWNERSHIP_COLS:
            continue
        if isinstance(pair, dict) and "new" in pair:
            out[field] = pair["new"]
    return out


def _row_exists(registry, table, raw_pk: str) -> bool:
    pk_col = crud_engine._pk_column(table)
    try:
        pk_value = crud_engine._coerce_value(pk_col, raw_pk)
    except (ValueError, TypeError):
        return False
    with registry.engine.connect() as conn:
        row = conn.execute(
            crud_engine.select(table).where(pk_col == pk_value)
        ).mappings().first()
    return row is not None


def _match_some_row(registry, table, new_vals: dict, actor_user_id) -> bool:
    """新增记录的 crash 收尾：主键未知时按意图值反查最近一行（含身份核对）。"""
    cols = table.c
    conds = []
    for field, value in new_vals.items():
        if field not in cols:
            continue
        if value is None:
            continue
        try:
            conds.append(cols[field] == crud_engine._coerce_value(cols[field], value))
        except (ValueError, TypeError):
            continue
    if "created_by" in cols and actor_user_id:
        conds.append(cols["created_by"] == actor_user_id)
    if not conds:
        return False
    pk_col = crud_engine._pk_column(table)
    stmt = crud_engine.select(table).where(*conds).order_by(pk_col.desc()).limit(1)
    with registry.engine.connect() as conn:
        row = conn.execute(stmt).mappings().first()
    return row is not None


def _row_matches(registry, table, raw_pk: str, target: dict) -> bool:
    pk_col = crud_engine._pk_column(table)
    try:
        pk_value = crud_engine._coerce_value(pk_col, raw_pk)
    except (ValueError, TypeError):
        return False
    with registry.engine.connect() as conn:
        row = conn.execute(
            crud_engine.select(table).where(pk_col == pk_value)
        ).mappings().first()
    if row is None:
        return False
    cols = table.c
    for field, new_value in target.items():
        if field not in cols:
            continue
        try:
            coerced = crud_engine._coerce_value(cols[field], new_value)
        except (ValueError, TypeError):
            coerced = new_value
        if not same(row[field], coerced):
            return False
    return True


def _pending_effective(registry, row: ActivityLog) -> bool | None:
    """判定一条 pending 业务日志是否已实际生效；无法判定返回 None（保留待下次）。"""
    table_name = row.object_table
    try:
        table = registry.table(table_name)
    except Exception:  # noqa: BLE001 表不存在/不可见 → 无法判定
        return None
    if row.action == "create":
        if row.object_pk:
            return _row_exists(registry, table, row.object_pk)
        return _match_some_row(registry, table, _changes_new(row), row.actor_user_id)
    if row.action == "delete":
        if not row.object_pk:
            return None
        return not _row_exists(registry, table, row.object_pk)
    if row.action == "update":
        if not row.object_pk:
            return None
        return _row_matches(registry, table, row.object_pk, _changes_new(row))
    return None


def reconcile_pending(app, min_age_seconds: float = 0) -> int:
    """启动对账（DR-03）：pending 业务日志按实际生效性收尾。

    生效 → committed；未生效 → 丢弃；无法判定（表缺失/判定条件不足）→ 保留下一次。
    返回本次处理（收尾或丢弃）的条数。
    """
    from server.crud import runtime

    Session = app.extensions["datamason_session_factory"]
    pending: dict[int, list[int]] = {}
    now = _store_now()
    with Session() as s:
        rows = (
            s.query(ActivityLog)
            .filter(ActivityLog.object_kind == "table")
            .filter(ActivityLog.status == "pending")
            .order_by(ActivityLog.id)
            .all()
        )
        for row in rows:
            if min_age_seconds and row.occurred_at is not None:
                age = (now - row.occurred_at).total_seconds()
                if age < min_age_seconds:
                    continue
            pending.setdefault(row.deployment_id, []).append(row.id)

    processed = 0
    for deployment_id, ids in pending.items():
        try:
            _deployment, registry, engine = runtime._registry(app, deployment_id, None)
        except Exception:  # noqa: BLE001 连接不可用 → 保留待下次
            continue
        try:
            with Session() as s:
                for log_id in ids:
                    row = s.get(ActivityLog, log_id)
                    if row is None:
                        continue
                    try:
                        effective = _pending_effective(registry, row)
                    except Exception:  # noqa: BLE001
                        effective = None
                    if effective is True:
                        row.status = "committed"
                        processed += 1
                    elif effective is False:
                        s.delete(row)
                        processed += 1
                    # None → 保留
                s.commit()
        finally:
            engine.dispose()
    return processed


def reconcile_pending_background(app) -> None:
    """启动时在后台线程执行对账（app.py 挂载）；异常只记日志不阻塞启动。"""
    import threading

    def _run():
        with app.app_context():
            try:
                done = reconcile_pending(app)
                if done:
                    app.logger.info("[audit] 启动对账：收尾 %d 条 pending 日志", done)
            except Exception as exc:  # noqa: BLE001
                app.logger.warning("[audit] 启动对账失败：%s", exc)

    threading.Thread(target=_run, daemon=True, name="audit-reconcile").start()