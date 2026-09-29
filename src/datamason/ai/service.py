"""AI 生成历史治理服务（M4-4）：统一写入 + 查询（筛选/统计）+ 清理。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func

from datamason.models import AiGeneration, Deployment

MAX_PAGE_SIZE = 200


def _session(app):
    return app.extensions["datamason_session_factory"]()


def record(app, kind: str, status: str, *, prompt_summary: str = "",
           result_summary: str = "", usage=None, deployment_id: int = 0) -> None:
    """统一写一条生成历史；埋点失败绝不阻断主流程。"""
    try:
        with _session(app) as s:
            s.add(AiGeneration(
                kind=kind,
                status=status,
                prompt_summary=(prompt_summary or "")[:1000],
                result_summary=(result_summary or "")[:1000],
                tokens_in=int(getattr(usage, "tokens_in", 0) or 0),
                tokens_out=int(getattr(usage, "tokens_out", 0) or 0),
                duration_ms=int(getattr(usage, "duration_ms", 0) or 0),
                deployment_id=int(deployment_id or 0),
            ))
            s.commit()
    except Exception:  # noqa: BLE001 治理埋点为旁路，失败不影响业务
        pass


def _apply_filters(query, *, kind, deployment_id, status):
    if kind:
        query = query.filter(AiGeneration.kind == kind)
    if deployment_id is not None:
        query = query.filter(AiGeneration.deployment_id == int(deployment_id))
    if status:
        query = query.filter(AiGeneration.status == status)
    return query


def list_generations(app, *, kind: str | None = None, deployment_id: int | None = None,
                     status: str | None = None, page: int = 1, page_size: int = 20) -> dict:
    """时间倒序分页 + 按 kind/部署/状态筛选；附统计（总数/用量合计/按 kind 计数）。"""
    page = max(int(page), 1)
    page_size = min(max(int(page_size), 1), MAX_PAGE_SIZE)
    with _session(app) as s:
        filtered = _apply_filters(
            s.query(AiGeneration), kind=kind, deployment_id=deployment_id, status=status)
        total = filtered.count()
        rows = (
            filtered.order_by(AiGeneration.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        sums = _apply_filters(
            s.query(func.coalesce(func.sum(AiGeneration.tokens_in), 0),
                    func.coalesce(func.sum(AiGeneration.tokens_out), 0),
                    func.coalesce(func.avg(AiGeneration.duration_ms), 0)),
            kind=kind, deployment_id=deployment_id, status=status,
        ).first()
        by_kind = dict(
            _apply_filters(s.query(AiGeneration.kind, func.count()),
                           kind=kind, deployment_id=deployment_id, status=status)
            .group_by(AiGeneration.kind).all()
        )
        names = {
            d.id: d.biz_name
            for d in s.query(Deployment).filter(
                Deployment.id.in_({r.deployment_id for r in rows if r.deployment_id})
            ).all()
        } if rows else {}
        # 筛选下拉候选：库中出现过生成记录的部署（不受当前筛选影响，便于切换）
        dep_pairs = (
            s.query(AiGeneration.deployment_id)
            .filter(AiGeneration.deployment_id > 0)
            .distinct()
            .all()
        )
        dep_ids = [did for (did,) in dep_pairs]
        dep_names = {
            d.id: d.biz_name
            for d in s.query(Deployment).filter(Deployment.id.in_(dep_ids or [-1])).all()
        }
        deployments = sorted(
            ({"id": did, "name": dep_names.get(did, f"部署 #{did}")} for did in dep_ids),
            key=lambda x: x["id"],
        )
        items = [_public(r, names) for r in rows]
    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "deployments": deployments,
        "stats": {
            "total": total,
            "tokens_in": int(sums[0] or 0),
            "tokens_out": int(sums[1] or 0),
            "avg_duration_ms": int(sums[2] or 0),
            "by_kind": by_kind,
        },
    }


def _public(row: AiGeneration, names: dict[int, str]) -> dict:
    return {
        "id": row.id,
        "kind": row.kind,
        "status": row.status,
        "prompt_summary": row.prompt_summary,
        "result_summary": row.result_summary,
        "tokens_in": row.tokens_in,
        "tokens_out": row.tokens_out,
        "duration_ms": row.duration_ms,
        "deployment_id": row.deployment_id,
        "deployment_name": names.get(row.deployment_id, ""),
        "created_at": row.created_at.isoformat(sep=" ") if row.created_at else "",
    }


def cleanup(app, *, ids: list[int] | None = None, kind: str | None = None,
            deployment_id: int | None = None, status: str | None = None,
            older_than_days: int | None = None) -> dict:
    """清理历史（仅删 ai_generations，不影响业务数据/页面）。

    必须给出条件（显式 ids 或至少一个筛选），避免误清全表。
    """
    with _session(app) as s:
        query = s.query(AiGeneration)
        if ids:
            query = query.filter(AiGeneration.id.in_([int(i) for i in ids]))
        else:
            if not any([kind, status, deployment_id is not None, older_than_days]):
                raise ValueError("请指定清理条件（ids 或 kind/status/deployment_id/older_than_days）")
            query = _apply_filters(
                query, kind=kind, deployment_id=deployment_id, status=status)
            if older_than_days is not None:
                cutoff = datetime.now(timezone.utc) - timedelta(days=int(older_than_days))
                query = query.filter(AiGeneration.created_at < cutoff)
        deleted = query.delete(synchronize_session=False)
        s.commit()
    return {"deleted": int(deleted or 0)}


def delete_one(app, gen_id: int) -> dict:
    with _session(app) as s:
        row = s.get(AiGeneration, gen_id)
        if row is None:
            raise KeyError("生成记录不存在")
        s.delete(row)
        s.commit()
    return {"ok": True}
