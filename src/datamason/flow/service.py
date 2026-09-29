"""流程规格 v0（M3-4，DR-06）：向导式流程（不是工作流引擎）。

一条流程 = 一串**页面步骤**（业务动词步骤名 + 页面引用 + 完成条件 + 状态字段/状态值）。
运行态给每个成员一条**游标**（flow_specs.progress_json：{user_id: index}）：

- 当前步 = 游标指向的步骤；完成条件满足才可点「下一步」；不满足时推进被拒绝。
- 「下一步」把本步页面表的**边界记录**（status_field 为空，或等于上一步状态值；
  操作员只动自己创建的记录）推进为本步 status_value，写 activity_logs（action=flow），
  游标 +1 →「状态字段随步推进」（如 待质检 → 已质检 → 已入库）。
- 完成条件 v0 只有一种：done_when={"type":"records","min":1} —— 本步页面表存在
  ≥ min 条「已到本步状态值 或 仍在边界」的记录（操作员=我的记录，创建人/管理员=全部）。

编排（工程室，创建人独有 DR-14）：新建流程时同步生成一张 kind=flow 的 PageSpec
（携带 flow_id），供菜单 kind=flow 挂载；菜单 ref_id 语义不变（仍指向 page_specs.id）。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from werkzeug.exceptions import Conflict

from datamason.crud import engine as crud_engine
from datamason.crud import runtime as crud_runtime
from datamason.members import service as member_service
from datamason.models import (
    Deployment,
    DeploymentMenu,
    DeploymentMenuGrant,
    FlowSpec,
    PageSpec,
    TableSpec,
)

_MAX_STEPS = 20


class FlowError(ValueError):
    """流程编排/运行校验错误 → 400。"""


def _session(app):
    return app.extensions["datamason_session_factory"]()


def _deployment(sess, deployment_id: int) -> Deployment:
    d = sess.get(Deployment, deployment_id)
    if d is None:
        raise KeyError("部署不存在")
    return d


def _load_flow(sess, deployment_id: int, flow_id: int) -> FlowSpec:
    f = sess.get(FlowSpec, flow_id)
    if f is None or f.deployment_id != deployment_id:
        raise KeyError("流程不存在")
    return f


def _load_page_map(sess, deployment_id: int) -> dict[int, dict]:
    specs = {
        t.id: t for t in sess.query(TableSpec).filter_by(deployment_id=deployment_id).all()
    }
    out: dict[int, dict] = {}
    for p in sess.query(PageSpec).filter_by(deployment_id=deployment_id).all():
        ts = specs.get(p.table_id)
        out[p.id] = {
            "kind": p.kind,
            "title": p.title,
            "table_id": p.table_id,
            "table": ts.name if ts else None,
            "table_biz_name": ts.biz_name if ts else None,
            "flow_id": p.flow_id,
        }
    return out


def _table_columns(sess, table_id: int | None) -> list[dict]:
    if table_id is None:
        return []
    ts = sess.get(TableSpec, table_id)
    if ts is None:
        return []
    return [
        c for c in ts.columns
        if not c.get("is_ownership") and not c.get("is_pk")
    ]


# ---------------- 编排（创建人独有，DR-14） ----------------

def _validate_steps(sess, deployment_id: int, steps: list) -> list[dict]:
    if not isinstance(steps, list):
        raise FlowError("steps 必须是步骤列表")
    if not steps or len(steps) > _MAX_STEPS:
        raise FlowError(f"步骤数量必须为 1-{_MAX_STEPS} 个")
    page_map = _load_page_map(sess, deployment_id)
    normalized: list[dict] = []
    for idx, raw in enumerate(steps):
        if not isinstance(raw, dict):
            raise FlowError(f"第 {idx + 1} 步格式错误")
        name = (raw.get("name") or "").strip()
        if not name or len(name) > 64:
            raise FlowError(f"第 {idx + 1} 步步骤名必须为 1-64 字符（用业务动词，如「收货登记」）")
        page_id = raw.get("page_id")
        pmap = page_map.get(page_id) if isinstance(page_id, int) else None
        if pmap is None:
            raise FlowError(f"第 {idx + 1} 步「{name}」指向的页面不属于该部署")
        if pmap["kind"] == "flow":
            raise FlowError(f"第 {idx + 1} 步「{name}」不能指向流程页，请选业务页面")
        done_when = raw.get("done_when") or {"type": "records", "min": 1}
        if (
            not isinstance(done_when, dict)
            or done_when.get("type") != "records"
            or not isinstance(done_when.get("min"), int)
            or done_when.get("min") < 1
        ):
            raise FlowError(f"第 {idx + 1} 步「{name}」完成条件无效（v0 仅支持 页面至少完成 N 条记录）")
        status_field = (raw.get("status_field") or "").strip() or None
        status_value = (raw.get("status_value") or "").strip() or None
        if status_value and not status_field:
            raise FlowError(f"第 {idx + 1} 步「{name}」绑定了推进值但没有状态字段")
        if status_field:
            if len(status_field) > 64:
                raise FlowError(f"第 {idx + 1} 步「{name}」状态字段名过长")
            cols = {c["name"] for c in _table_columns(sess, pmap["table_id"])}
            if status_field not in cols:
                raise FlowError(
                    f"第 {idx + 1} 步「{name}」状态字段「{status_field}」不在页面表"
                    f"「{pmap['table_biz_name'] or pmap['table']}」中"
                )
        if status_value and len(status_value) > 64:
            raise FlowError(f"第 {idx + 1} 步「{name}」状态值过长（最长 64 字符）")
        normalized.append({
            "name": name,
            "page_id": page_id,
            "done_when": {"type": "records", "min": int(done_when["min"])},
            "status_field": status_field,
            "status_value": status_value,
        })
    return normalized


def list_flows(app, deployment_id: int) -> dict:
    with _session(app) as s:
        _deployment(s, deployment_id)
        flow_pages = {
            p.flow_id: p
            for p in s.query(PageSpec)
            .filter(PageSpec.deployment_id == deployment_id, PageSpec.flow_id.isnot(None))
            .all()
        }
        page_map = _load_page_map(s, deployment_id)
        flows = (
            s.query(FlowSpec)
            .filter_by(deployment_id=deployment_id)
            .order_by(FlowSpec.id)
            .all()
        )
        return {"flows": [_flow_spec(f, page_map, flow_pages.get(f.id)) for f in flows]}


def _flow_spec(f: FlowSpec, page_map: dict[int, dict], flow_page) -> dict:
    steps = []
    for i, st in enumerate(f.steps):
        pmap = page_map.get(st.get("page_id")) or {}
        steps.append({
            "index": i,
            "name": st.get("name", ""),
            "page_id": st.get("page_id"),
            "page_title": pmap.get("title") or "",
            "kind": pmap.get("kind") or "",
            "table": pmap.get("table"),
            "table_biz_name": pmap.get("table_biz_name"),
            "done_when": {"type": "records", "min": (st.get("done_when") or {}).get("min", 1)},
            "status_field": st.get("status_field"),
            "status_value": st.get("status_value"),
        })
    return {
        "id": f.id,
        "name": f.name,
        "version": f.version,
        "page_id": flow_page.id if flow_page else None,
        "steps": steps,
    }


def save_flow(app, deployment_id: int, flow_id: int | None, name: str,
              steps: list, actor_user_id: int) -> dict:
    from datamason.audit.service import log_system

    name = (name or "").strip()
    if not name or len(name) > 64:
        raise FlowError("流程名称必须为 1-64 字符")
    with _session(app) as s:
        _deployment(s, deployment_id)
        normalized = _validate_steps(s, deployment_id, steps)
        if flow_id is None:
            f = FlowSpec(deployment_id=deployment_id, name=name,
                         steps_json=json.dumps(normalized, ensure_ascii=False),
                         progress_json="{}", version=1)
            s.add(f)
            s.flush()
            fp = PageSpec(deployment_id=deployment_id, kind="flow", access="member",
                          table_id=None, title=name, layout_json="{}", flow_id=f.id)
            s.add(fp)
            s.flush()
            flow_id = f.id
            action = "新增"
        else:
            f = _load_flow(s, deployment_id, flow_id)
            f.name = name
            f.steps_json = json.dumps(normalized, ensure_ascii=False)
            f.version = (f.version or 1) + 1
            fp = (
                s.query(PageSpec)
                .filter_by(deployment_id=deployment_id, flow_id=flow_id)
                .first()
            )
            if fp is not None:
                fp.title = name
            action = "更新"
        step_names = [st["name"] for st in normalized]
        s.commit()
    log_system(
        app, deployment_id, "flow", actor_user_id=actor_user_id,
        object_kind="flow", object_table="flow_specs", object_pk=str(flow_id),
        object_label=f"流程「{name}」{action}（{len(step_names)} 步）",
        changes={"action": "upsert", "version": f.version, "steps": step_names},
    )
    with _session(app) as s:
        page_map = _load_page_map(s, deployment_id)
        return _flow_spec(f, page_map, fp)


def delete_flow(app, deployment_id: int, flow_id: int, actor_user_id: int) -> dict:
    from datamason.audit.service import log_system

    with _session(app) as s:
        f = _load_flow(s, deployment_id, flow_id)
        name = f.name
        flow_pages = (
            s.query(PageSpec)
            .filter_by(deployment_id=deployment_id, flow_id=flow_id)
            .all()
        )
        page_ids = [fp.id for fp in flow_pages]
        if page_ids:
            menus = (
                s.query(DeploymentMenu)
                .filter(DeploymentMenu.deployment_id == deployment_id,
                        DeploymentMenu.ref_id.in_(page_ids))
                .all()
            )
            for m in menus:
                s.query(DeploymentMenuGrant).filter_by(menu_id=m.id).delete()
                s.delete(m)
            for fp in flow_pages:
                s.delete(fp)
        s.delete(f)
        s.commit()
    log_system(
        app, deployment_id, "flow", actor_user_id=actor_user_id,
        object_kind="flow", object_table="flow_specs", object_pk=str(flow_id),
        object_label=f"流程「{name}」已删除",
        changes={"action": "delete", "name": name},
    )
    return {"ok": True}


# ---------------- 运行态（向导式解释器） ----------------

def _condition_text(step: dict) -> str:
    minimum = (step.get("done_when") or {}).get("min", 1)
    col, value = step.get("status_field"), step.get("status_value")
    if col and value:
        return f"本步骤页面需有 ≥{minimum} 条记录推进为「{value}」"
    return f"本步骤页面需完成 ≥{minimum} 条记录"


def _registry(app, deployment_id: int, uid: int):
    """复用 CRUD 注册表：操作员仅授权表；成员校验在此完成。"""
    with _session(app) as s:
        d = s.get(Deployment, deployment_id)
        if d is None:
            raise KeyError("部署不存在")
        role = member_service.assert_member(s, d, uid)
    _d, registry, engine = crud_runtime._registry(app, deployment_id, uid)
    return role, registry, engine


def _cursor(f: FlowSpec, uid: int) -> int:
    try:
        progress = json.loads(f.progress_json or "{}")
        c = int(progress.get(str(uid), 0))
    except (ValueError, TypeError):
        c = 0
    return c


def _store_cursor(app, f: FlowSpec, uid: int, index: int) -> None:
    with _session(app) as s:
        row = s.get(FlowSpec, f.id)
        if row is None:
            return
        try:
            progress = json.loads(row.progress_json or "{}")
        except (ValueError, TypeError):
            progress = {}
        progress[str(uid)] = index
        row.progress_json = json.dumps(progress, ensure_ascii=False)
        s.commit()


def _scope_cond(role: str, table, uid: int):
    """操作员强制「我的记录」（DR-05）；创建人/管理员全量。"""
    if role == "operator" and "created_by" in table.c:
        return table.c.created_by == uid
    return None


def _boundary_conds(table, step: dict, prev: dict | None) -> list:
    """本步「边界记录」= status 为空 或 等于上一步状态值（同字段才接链）。"""
    col = step.get("status_field")
    if not col or col not in table.c:
        return []
    out = [table.c[col].is_(None)]
    if prev and prev.get("status_field") == col and prev.get("status_value"):
        out.append(table.c[col] == prev["status_value"])
    return out


def _reached_conds(table, step: dict) -> list:
    col = step.get("status_field")
    if col and col in table.c and step.get("status_value"):
        return [table.c[col] == step["status_value"]]
    return []


def _count_rows(engine, table, conds: list) -> int:
    with engine.connect() as conn:
        return conn.execute(
            select(func.count()).select_from(table).where(*conds)
        ).scalar_one()


def _step_met(registry, engine, role: str, uid: int, table_name: str,
              step: dict, prev: dict | None) -> tuple[bool, int]:
    """完成条件判定；表不可见/异常按未完成处理（不阻塞流程返回）。"""
    try:
        table = registry.table(table_name)
    except Exception:  # noqa: BLE001
        return False, 0
    conds = []
    scope = _scope_cond(role, table, uid)
    if scope is not None:
        conds.append(scope)
    # 完成 = 已到达本步状态值 或 仍在边界（空/上一步值）
    inner = _reached_conds(table, step) + _boundary_conds(table, step, prev)
    if inner:
        conds.append(or_(*inner))
    n = _count_rows(engine, table, conds)
    minimum = (step.get("done_when") or {}).get("min", 1)
    return n >= minimum, n


def runtime_flow(app, deployment_id: int, flow_id: int, uid: int) -> dict:
    role, registry, engine = _registry(app, deployment_id, uid)
    try:
        with _session(app) as s:
            f = _load_flow(s, deployment_id, flow_id)
            d = _deployment(s, deployment_id)
            if d.status != "running":
                raise Conflict(description="部署未处于运行态，无法使用流程向导")
            flow_page = (
                s.query(PageSpec)
                .filter_by(deployment_id=deployment_id, flow_id=flow_id)
                .first()
            )
            if role == "operator":
                _assert_flow_visible(s, d, flow_page, uid)
            page_map = _load_page_map(s, deployment_id)
        c = _cursor(f, uid)
        done_total = c >= len(f.steps)
        steps = f.steps
        rows = []
        for i, raw in enumerate(steps):
            pmap = page_map.get(raw.get("page_id") or -1, {})
            state = "done" if (i < c or done_total) else "current" if i == c else "pending"
            rows.append({
                "index": i,
                "name": raw.get("name", ""),
                "page_id": raw.get("page_id"),
                "page_title": pmap.get("title") or "",
                "kind": pmap.get("kind") or "",
                "table": pmap.get("table") or "",
                "table_biz_name": pmap.get("table_biz_name") or "",
                "status_field": raw.get("status_field"),
                "status_value": raw.get("status_value"),
                "condition": _condition_text(raw),
                "state": state,
                "met": done_total,
            })
        # 当前步条件独立判定（其余步 met 仅用于提示）
        for row in rows:
            if row["state"] == "current":
                prev = steps[row["index"] - 1] if row["index"] > 0 else None
                ok, _n = _step_met(registry, engine, role, uid,
                                   row["table"], steps[row["index"]], prev)
                row["met"] = ok
        return {
            "id": f.id,
            "name": f.name,
            "version": f.version,
            "done": done_total,
            "current_index": min(c, len(steps)),
            "page_id": flow_page.id if flow_page else None,
            "steps": rows,
        }
    finally:
        engine.dispose()


def advance_flow(app, deployment_id: int, flow_id: int, uid: int) -> dict:
    from datamason.audit.service import AuditSession

    role, registry, engine = _registry(app, deployment_id, uid)
    try:
        with _session(app) as s:
            f = _load_flow(s, deployment_id, flow_id)
            d = _deployment(s, deployment_id)
            if d.status != "running":
                raise Conflict(description="部署未处于运行态，无法推进流程")
            flow_page = (
                s.query(PageSpec)
                .filter_by(deployment_id=deployment_id, flow_id=flow_id)
                .first()
            )
            if role == "operator":
                _assert_flow_visible(s, d, flow_page, uid)
            page_map = _load_page_map(s, deployment_id)
        c = _cursor(f, uid)
        steps = f.steps
        if c >= len(steps):
            raise Conflict(description="流程已全部完成，无需继续推进")
        step = steps[c]
        prev = steps[c - 1] if c > 0 else None
        pmap = page_map.get(step.get("page_id") or -1, {})
        table_name = pmap.get("table") or ""
        if not table_name:
            raise Conflict(description=f"步骤「{step.get('name')}」绑定的页面表不存在")
        try:
            table = registry.table(table_name)
        except Exception:  # noqa: BLE001
            raise Conflict(description=f"步骤「{step.get('name')}」绑定的页面表不可见")

        ok, _n = _step_met(registry, engine, role, uid, table_name, step, prev)
        if not ok:
            raise Conflict(description=(
                f"当前步骤「{step.get('name')}」的完成条件不满足："
                f"{_condition_text(step)}；请先在页面完成操作再点「下一步」"))

        writes = 0
        affected_pks: list[str] = []
        col_name = step.get("status_field")
        col = table.c.get(col_name) if col_name else None
        conds = []
        scope = _scope_cond(role, table, uid)
        if scope is not None:
            conds.append(scope)
        boundary = _boundary_conds(table, step, prev)
        if boundary:
            conds.append(or_(*boundary))

        will_write = col is not None and bool(step.get("status_value"))
        if will_write:
            # ①' 读边界记录主键（对账用；只读，不改数据）
            pk_col = list(table.primary_key.columns)[0]
            with engine.connect() as conn:
                affected_pks = [
                    str(row[0])
                    for row in conn.execute(select(pk_col).select_from(table).where(*conds)).all()
                ]

        # ① 两阶段日志（DR-03）：先写 pending，业务写成功后再置 committed
        audit = AuditSession(app, deployment_id, uid)
        log_id = audit.begin(
            "flow", table_name, object_kind="flow", object_pk=str(flow_id),
            object_label=f"流程「{f.name}」·{step.get('name')} 完成",
            changes={"step_name": step.get("name"), "step_index": c,
                     "status_field": step.get("status_field"),
                     "status_value": step.get("status_value"),
                     "affected_pks": affected_pks},
        )
        try:
            # ② 业务写（状态字段推进）
            if will_write:
                values = {col_name: step["status_value"]}
                if "updated_by" in table.c:
                    values["updated_by"] = uid
                if "updated_at" in table.c:
                    values["updated_at"] = datetime.now(timezone.utc)
                with engine.begin() as conn:
                    result = conn.execute(table.update().where(*conds).values(**values))
                writes = result.rowcount or 0
        except Exception:
            audit.abort(log_id)  # 业务失败 → 删 pending（无日志）
            raise
        # ③ committed（补 records_advanced）
        audit.commit(log_id, object_pk=str(flow_id), changes={
            "step_name": step.get("name"), "step_index": c,
            "status_field": step.get("status_field"),
            "status_value": step.get("status_value"),
            "affected_pks": affected_pks,
            "records_advanced": writes,
        })
        _store_cursor(app, f, uid, c + 1)
        return {"ok": True, "step": step.get("name"), "current_index": c + 1,
                "done": c + 1 >= len(steps), "records_advanced": writes}
    finally:
        engine.dispose()


def _assert_flow_visible(sess, d: Deployment, flow_page: PageSpec | None, uid: int) -> None:
    """操作员只能访问被授权菜单挂载的流程（DR-02 功能级裁剪）。"""
    if flow_page is None:
        raise KeyError("流程不存在")
    granted = member_service.granted_menu_ids(sess, d, uid)
    if not granted:
        raise member_service.PermissionDenied("该流程尚未授权给你，无法访问")
    visible = any(
        m.ref_id == flow_page.id
        for m in sess.query(DeploymentMenu).filter_by(deployment_id=d.id).all()
        if m.id in granted
    )
    if not visible:
        raise member_service.PermissionDenied("该流程尚未授权给你，无法访问")