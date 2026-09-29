"""问数据后端（M4-1，P3）：自然语言 → 只读 SELECT → 行集 + 一句话结论。

对齐《设计细化》§七「问数据机制」与 §三「NL 生成数据查询 / 安全检查模式」：
- schema context 由该部署 `table_specs` 确定性组装（表/列/类型/主外键/业务名 + 少量采样值）；
- LLM 生成 SELECT（temperature=0，失败用中文原因回喂重试，上限 2 次，同建表链路）；
- **sqlglot 只读门**（继承 superset-lite `generate_sql` 安全设计 + DBX 安全检查模式）：
  仅单条 SELECT/SELECT 集合运算、表白名单=该部署声明表、禁多语句/注释/DML/DDL/跨库限定名，
  并强制外层 LIMIT 兜底；字符串/注释由 sqlglot 解析归一化后再执行（不直接执行模型原文）；
- dataaccess 执行（参数化 + 行数上限）；一句话结论（LLM，失败回退确定性摘要）；
- 每次问答写 `ai_generations`（kind=qa，token/duration 埋点）；
- **纯只读浏览不进 activity_logs**（《设计细化》§五：只读浏览不记）。
"""
from __future__ import annotations

import json

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from werkzeug.exceptions import Conflict

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from datamason.llm.client import LlmCallError, Usage
from datamason.members import service as member_service
from datamason.models import (
    Connection,
    Deployment,
    DeploymentMenu,
    DeploymentMenuGrant,
    PageSpec,
    TableSpec,
)

# 结果集硬上限（与 CRUD 列表 page_size 上限一致）；模型给出的 LIMIT 之外再兜一层
MAX_ROWS = 200
# 生成失败回喂重试上限（同 table_spec 链路）
MAX_RETRY = 2
# schema context 采样列数量上限（LIMIT 3，避免大表全扫）
_MAX_SAMPLED_COLS = 60

# 只读门：任何写操作/DDL/事务/命令节点一律拒绝
_WRITE_NODES = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge,
    exp.Create, exp.Drop, exp.Alter, exp.TruncateTable,
    exp.Grant, exp.Revoke, exp.Copy,
    exp.Transaction, exp.Commit, exp.Rollback,
    exp.Command,
)
# 只读门：文件/系统类危险函数
_FORBIDDEN_FUNCS = {
    "load_file", "readfile", "writefile",
    "pg_read_file", "pg_read_binary_file", "pg_ls_dir",
    "sys_eval", "sys_exec", "sys_read_file",
}

_DIALECT_MAP = {"postgresql": "postgres", "mysql": "mysql", "sqlite": "sqlite"}

QA_SYSTEM_PROMPT = """你是数据分析助手，把用户的业务问题翻译成一条**只读 SQL 查询**，并给出可选的图表规格。
只输出一个 JSON 对象，不要任何解释或 markdown 代码块标记：
{"sql": "一条 SELECT 语句", "title": "报表标题（不超过 20 字）",
 "chart": {"type": "bar|line|pie", "x": "维度列名", "y": "数值列名"}}
硬性规则：
1. 只能是单条 SELECT（或 SELECT 的 UNION/EXCEPT/INTERSECT）；禁止 INSERT/UPDATE/DELETE/DDL/PRAGMA 等任何写操作或多条语句；
2. 只能查询下面列出的表，表名不要加库名/schema 前缀；
3. 禁止使用 SQL 注释；字符串常量里的中文原样保留；
4. 结果集不要过大，建议加合适的 WHERE 与 LIMIT（系统另有上限兜底）；
5. 这是只读查询，不要尝试修改任何数据；
6. chart 为可选提示：当结果像「一个维度 + 一个数值度量」时给 type/x/y（x/y 必须是该 SELECT
   返回的列名）；否则把 type 设为 null（系统会退化为表格呈现）。"""

CHART_TYPES = ("bar", "line", "pie")


CONCLUSION_SYSTEM_PROMPT = """你是数据分析助手，用一句简洁的中文总结下面的查询结果。
不要输出 SQL、markdown 或额外解释，只输出 JSON：{"conclusion": "一句话结论"}"""


class QaError(ValueError):
    """问数据校验/生成/执行错误 → 400。"""


def _session(app):
    return app.extensions["datamason_session_factory"]()


def _to_sqlglot_dialect(dialect: str) -> str:
    return _DIALECT_MAP.get((dialect or "").lower(), "sqlite")


def _is_textual(col: dict) -> bool:
    return str(col.get("type", "")).upper().startswith(("VARCHAR", "CHAR", "TEXT", "STRING"))


def _add_usage(total: Usage, usage: Usage | None) -> None:
    if usage is None:
        return
    total.tokens_in += usage.tokens_in or 0
    total.tokens_out += usage.tokens_out or 0
    total.duration_ms += usage.duration_ms or 0


# ---------------- schema context（确定性组装，可含采样值） ----------------

def _sample_values(registry, engine: Engine, table_name: str, column_name: str) -> list[str]:
    """取该列少量非空采样值（LIMIT 3，不做 DISTINCT 以免大表全扫）；失败静默跳过。"""
    try:
        table = registry.table(table_name)
        if column_name not in table.c:
            return []
        column = table.c[column_name]
        with engine.connect() as conn:
            rows = conn.execute(
                select(column).where(column.isnot(None)).limit(3)
            ).fetchall()
        out: list[str] = []
        for (value,) in rows:
            text = str(value)
            if text and text not in out:
                out.append(text)
        return out
    except Exception:  # noqa: BLE001 采样是增强项，任何失败都不影响问数据
        return []


def build_schema_context(app, deployment_id: int, registry, engine: Engine) -> str:
    """把该部署的 table_specs 组装成给 LLM 看的 schema 文本（含主外键与采样值）。"""
    with _session(app) as s:
        specs = (
            s.query(TableSpec)
            .filter_by(deployment_id=deployment_id)
            .order_by(TableSpec.id)
            .all()
        )
        data = [(t.name, t.biz_name, t.columns) for t in specs]

    lines: list[str] = []
    sampled = 0
    for name, biz, cols in data:
        lines.append(f"表 {name}（{biz or name}）:")
        for c in cols:
            col_name = c.get("name", "")
            tags: list[str] = []
            if c.get("is_pk"):
                tags.append("主键")
            if c.get("is_fk"):
                tags.append(f"外键→{c.get('ref_table')}.{c.get('ref_column')}")
            if c.get("is_ownership"):
                tags.append("系统归属列")
            tag_text = f" [{'，'.join(tags)}]" if tags else ""
            sample_text = ""
            if (sampled < _MAX_SAMPLED_COLS and col_name
                    and not c.get("is_pk") and _is_textual(c)):
                values = _sample_values(registry, engine, name, col_name)
                if values:
                    sample_text = " 示例: " + ", ".join(values)
                    sampled += 1
            lines.append(
                f"  - {col_name} {c.get('type', '')}{tag_text}"
                f"（{c.get('biz_name') or col_name}）{sample_text}"
            )
    return "\n".join(lines)


# ---------------- sqlglot 只读门 ----------------

def enforce_readonly(sql: str, allowed_tables, dialect: str) -> str:
    """校验并归一化只读 SQL；不通过抛 QaError。返回 sqlglot 重生成的语句（无注释/无分号）。"""
    raw = (sql or "").strip()
    if not raw:
        raise QaError("模型未生成 SQL")
    if "\x00" in raw:
        raise QaError("SQL 含非法字符")
    read_dialect = _to_sqlglot_dialect(dialect)
    try:
        statements = [s for s in sqlglot.parse(raw, read=read_dialect) if s is not None]
    except ParseError as exc:
        raise QaError(f"SQL 解析失败：{exc}") from exc

    if len(statements) != 1:
        raise QaError("只读门：只允许单条 SQL 语句")
    expr = statements[0]
    if not isinstance(expr, (exp.Select, exp.SetOperation)):
        raise QaError("只读门：只允许 SELECT 只读查询")

    for node_cls in _WRITE_NODES:
        if expr.find(node_cls) is not None:
            raise QaError("只读门：禁止写操作 / DDL / 事务 / 命令")
    if isinstance(expr, exp.Select):
        if expr.args.get("into"):
            raise QaError("只读门：禁止 SELECT ... INTO")
        if expr.args.get("locks"):
            raise QaError("只读门：禁止锁定查询（FOR UPDATE）")

    cte_names = {c.alias_or_name.lower() for c in expr.find_all(exp.CTE) if c.alias_or_name}
    allowed = {str(t).lower() for t in (allowed_tables or [])}
    for tbl in expr.find_all(exp.Table):
        lower = tbl.name.lower()
        if tbl.db or tbl.catalog:
            raise QaError(f"只读门：不允许库/schema 限定名（{tbl.name}）")
        if lower in cte_names:
            continue
        if lower not in allowed:
            raise QaError(f"只读门：表「{tbl.name}」不在本次可查范围内")

    for fn in expr.find_all(exp.Anonymous):
        if (fn.name or "").lower() in _FORBIDDEN_FUNCS:
            raise QaError(f"只读门：禁止使用函数 {fn.name}")

    return expr.sql(dialect=read_dialect)


def _wrap_limit(inner_sql: str, max_rows: int = MAX_ROWS) -> str:
    """外层包一层兜底 LIMIT（内层自带 LIMIT 时取更小语义的并集，不会放大结果）。"""
    return f"SELECT * FROM ({inner_sql}) AS _dm_qa LIMIT {max_rows}"


def _execute(engine: Engine, inner_sql: str, dialect: str, max_rows: int = MAX_ROWS) -> dict:
    from sqlalchemy import text

    from datamason.audit.service import jsonable

    wrapped = _wrap_limit(inner_sql, max_rows)
    with engine.connect() as conn:
        result = conn.execute(text(wrapped))
        columns = list(result.keys())
        raw_rows = result.fetchall()
    rows = [
        {col: jsonable(value) for col, value in zip(columns, row)}
        for row in raw_rows[:max_rows]
    ]
    return {"columns": columns, "rows": rows}


# ---------------- LLM 生成（SELECT / 结论） ----------------

def _generate_sql(client, dialect: str, schema_text: str, question: str,
                  last_error: str = "") -> tuple[str, str, object, Usage]:
    system = (
        f"{QA_SYSTEM_PROMPT}\n\n方言：{_to_sqlglot_dialect(dialect)}\n"
        f"可用表结构：\n{schema_text}"
    )
    prompt = f"业务问题：{question}\n请输出只读 SQL 的 JSON。"
    if last_error:
        prompt += f"\n\n上一次生成的 SQL 不可用：{last_error}\n请修正后重新输出完整 JSON。"
    parsed, usage = client.chat_json(system, prompt)
    if not isinstance(parsed, dict):
        raise QaError("模型返回不是 JSON 对象")
    sql = str(parsed.get("sql") or "").strip()
    title = str(parsed.get("title") or "").strip()[:40] or question[:20]
    if not sql:
        raise QaError("模型未生成 SQL")
    return sql, title, parsed.get("chart"), usage


def _validate_chart(chart_raw: object, columns: list[str]) -> dict | None:
    """图表规格结构化校验：类型白名单 + x/y 必须是本次结果列名。

    只做结构校验（不做数值化判断——那由前端在拿到真实行后决定是否降级为表格）。
    """
    if not isinstance(chart_raw, dict):
        return None
    ctype = str(chart_raw.get("type") or "").lower()
    if ctype not in CHART_TYPES:
        return None
    x, y = chart_raw.get("x"), chart_raw.get("y")
    if not isinstance(x, str) or not isinstance(y, str):
        return None
    if x not in columns or y not in columns or x == y:
        return None
    return {"type": ctype, "x": x, "y": y}



def _conclusion(client, question: str, columns: list[str], rows: list[dict],
                total: Usage) -> str:
    """一句话结论：LLM 生成；任何失败回退确定性摘要（不影响主结果）。"""
    if not rows:
        return "没有查询到符合条件的数据。"
    if client is None:
        return f"共 {len(rows)} 行数据。"
    try:
        sample = rows[:20]
        prompt = (
            f"业务问题：{question}\n列：{columns}\n"
            f"结果（最多 20 行）：{sample}\n请输出一句话中文结论。"
        )
        parsed, usage = client.chat_json(CONCLUSION_SYSTEM_PROMPT, prompt)
        _add_usage(total, usage)
        if isinstance(parsed, dict):
            text = str(parsed.get("conclusion") or "").strip()
            if text:
                return text
    except Exception:  # noqa: BLE001 结论失败回退确定性摘要
        pass
    return f"共 {len(rows)} 行数据。"


# ---------------- 埋点（ai_generations，M4-4 统一治理） ----------------

def _record_generation(app, deployment_id: int, status: str, prompt_summary: str,
                       result_summary: str, usage: Usage) -> None:
    from datamason.ai import service as ai_service

    ai_service.record(
        app, "qa", status, prompt_summary=prompt_summary,
        result_summary=result_summary, usage=usage, deployment_id=deployment_id)


# ---------------- 入口 ----------------

def ask(app, deployment_id: int, question: str, uid: int) -> dict:
    """问一句 → 只读查询 → {结论, 表格数据, SQL}；并写 ai_generations（kind=qa）。"""
    from datamason.crud import runtime as crud_runtime
    from datamason.settings.service import get_default_llm_client

    question = (question or "").strip()
    if not question:
        raise QaError("请输入要问的问题")
    if len(question) > 500:
        raise QaError("问题过长（最多 500 字）")

    with _session(app) as s:
        d = s.get(Deployment, deployment_id)
        if d is None:
            raise KeyError("部署不存在")
        role = member_service.assert_member(s, d, uid)
        if role == "operator":
            raise member_service.PermissionDenied("问数据暂不对操作员开放")
        if d.status != "running":
            raise Conflict(description="部署未处于运行态，无法使用问数据")
        conn = s.get(Connection, d.connection_id) if d.connection_id else None
        dialect = conn.dialect if conn else "sqlite"

    client = get_default_llm_client(app)
    if client is None:
        raise QaError("尚未配置默认 LLM 模型，请先到「设置 → 模型管理」添加并测试")

    _d, registry, engine = crud_runtime._registry(app, deployment_id, uid)
    usage = Usage()
    try:
        schema_text = build_schema_context(app, deployment_id, registry, engine)
        last_error = ""
        inner_sql = title = ""
        chart_raw: object = None
        attempts = 0
        for attempt in range(MAX_RETRY + 1):
            attempts = attempt + 1
            try:
                sql_raw, title, chart_raw, call_usage = _generate_sql(
                    client, dialect, schema_text, question, last_error)
                _add_usage(usage, call_usage)
                inner_sql = enforce_readonly(sql_raw, registry.allowed_tables, dialect)
                break
            except (LlmCallError, QaError) as exc:
                last_error = str(exc)
                if attempt == MAX_RETRY:
                    _record_generation(app, deployment_id, "error", question, last_error, usage)
                    raise QaError(f"经过 {attempts} 次生成仍不可用：{last_error}") from exc

        try:
            result = _execute(engine, inner_sql, dialect)
            conclusion = _conclusion(client, question, result["columns"], result["rows"], usage)
        except Exception as exc:  # noqa: BLE001 执行/总结失败也留痕（M4-4 各生成点 status）
            _record_generation(app, deployment_id, "error", question,
                               f"执行/总结失败：{exc}", usage)
            raise
        _record_generation(
            app, deployment_id, "ok", question, f"{title}（{len(result['rows'])} 行）", usage)
        return {
            "question": question,
            "title": title,
            "sql": inner_sql,
            "row_limit": MAX_ROWS,
            "columns": result["columns"],
            "rows": result["rows"],
            "row_count": len(result["rows"]),
            "conclusion": conclusion,
            "chart": _validate_chart(chart_raw, result["columns"]),
            "attempts": attempts,
        }
    finally:
        engine.dispose()


# ---------------- 钉住报表（M4-3，dashboard 页） ----------------

def _normalize_chart(chart: object) -> dict | None:
    """钉住时对图表规格做结构归一（类型白名单 + x/y 非空且不同）；不合法返回 None。"""
    if not isinstance(chart, dict):
        return None
    ctype = str(chart.get("type") or "").lower()
    x, y = chart.get("x"), chart.get("y")
    if ctype in CHART_TYPES and isinstance(x, str) and isinstance(y, str) and x and y and x != y:
        return {"type": ctype, "x": x, "y": y}
    return None


def _pins(sess, deployment_id: int) -> list[PageSpec]:
    return (
        sess.query(PageSpec)
        .filter_by(deployment_id=deployment_id, kind="dashboard")
        .order_by(PageSpec.id)
        .all()
    )


def list_pins(app, deployment_id: int) -> dict:
    with _session(app) as s:
        if s.get(Deployment, deployment_id) is None:
            raise KeyError("部署不存在")
        menus = {
            m.ref_id: m
            for m in s.query(DeploymentMenu).filter_by(deployment_id=deployment_id).all()
        }
        out = []
        for p in _pins(s, deployment_id):
            try:
                layout = json.loads(p.layout_json or "{}")
            except (ValueError, TypeError):
                layout = {}
            menu = menus.get(p.id)
            out.append({
                "page_id": p.id,
                "menu_id": menu.id if menu else None,
                "title": p.title,
                "sql": layout.get("sql", ""),
                "chart": layout.get("chart"),
                "question": layout.get("question", ""),
            })
        return {"pins": out}


def pin_report(app, deployment_id: int, uid: int, title: str, sql: str,
               chart: object = None, question: str = "") -> dict:
    """把一条只读查询钉成常驻报表：写 page_specs(kind=dashboard) + 挂菜单（structure 埋点）。

    SQL 重新过只读门/白名单后才落库；同一 SQL 已钉住则幂等返回（不重复建页面/菜单）。
    新菜单默认不授权给操作员（M2-5 体系约束沿用）。
    """
    from datamason.crud import runtime as crud_runtime
    from datamason.audit.service import log_system

    title = (title or "").strip()[:64] or "报表"
    with _session(app) as s:
        d = s.get(Deployment, deployment_id)
        if d is None:
            raise KeyError("部署不存在")
        conn = s.get(Connection, d.connection_id) if d.connection_id else None
        dialect = conn.dialect if conn else "sqlite"

    _d, registry, engine = crud_runtime._registry(app, deployment_id, uid)
    try:
        inner = enforce_readonly(sql, registry.allowed_tables, dialect)
    finally:
        engine.dispose()
    norm_chart = _normalize_chart(chart)
    layout_json = json.dumps(
        {"sql": inner, "chart": norm_chart, "question": question or ""},
        ensure_ascii=False)

    with _session(app) as s:
        for p in _pins(s, deployment_id):
            try:
                existing = json.loads(p.layout_json or "{}")
            except (ValueError, TypeError):
                existing = {}
            if existing.get("sql") == inner:
                menu = (
                    s.query(DeploymentMenu)
                    .filter_by(deployment_id=deployment_id, ref_id=p.id)
                    .first()
                )
                return {"page_id": p.id, "menu_id": menu.id if menu else None,
                        "title": p.title, "duplicate": True}
        page = PageSpec(deployment_id=deployment_id, kind="dashboard", access="member",
                        table_id=None, title=title, layout_json=layout_json)
        s.add(page)
        s.flush()
        max_order = (
            s.query(func.max(DeploymentMenu.order_index))
            .filter(DeploymentMenu.deployment_id == deployment_id)
            .scalar()
        )
        menu = DeploymentMenu(
            deployment_id=deployment_id, label=title, kind="dashboard",
            ref_id=page.id, order_index=int(max_order or 0) + 1,
        )
        s.add(menu)
        s.flush()
        result = {"page_id": page.id, "menu_id": menu.id, "title": title, "duplicate": False}
        s.commit()

    log_system(app, deployment_id, "structure", actor_user_id=uid,
               object_kind="page", object_table="page_specs", object_pk=str(result["page_id"]),
               object_label=f"钉住报表「{title}」",
               changes={"action": "pin", "sql": inner})
    return result


def unpin_report(app, deployment_id: int, page_id: int, uid: int) -> dict:
    """取消钉住：删除该报表页面 + 其挂载菜单 + 菜单授权（structure 埋点）。"""
    from datamason.audit.service import log_system

    with _session(app) as s:
        page = s.get(PageSpec, page_id)
        if page is None or page.deployment_id != deployment_id or page.kind != "dashboard":
            raise KeyError("报表不存在")
        title = page.title
        for menu in (
            s.query(DeploymentMenu).filter_by(deployment_id=deployment_id, ref_id=page_id).all()
        ):
            s.query(DeploymentMenuGrant).filter_by(menu_id=menu.id).delete()
            s.delete(menu)
        s.delete(page)
        s.commit()

    log_system(app, deployment_id, "structure", actor_user_id=uid,
               object_kind="page", object_table="page_specs", object_pk=str(page_id),
               object_label=f"取消钉住报表「{title}」", changes={"action": "unpin"})
    return {"ok": True}


def run_dashboard(app, deployment_id: int, page_id: int, uid: int) -> dict:
    """运行态重跑报表：只读门复检 + 白名单后执行 SQL，返回最新行集 + 图表规格。

    报表是成员页面；操作员须被授权挂载该报表的菜单（DR-02），否则 403。
    """
    from datamason.crud import runtime as crud_runtime

    with _session(app) as s:
        d = s.get(Deployment, deployment_id)
        if d is None:
            raise KeyError("部署不存在")
        role = member_service.assert_member(s, d, uid)
        if d.status != "running":
            raise Conflict(description="部署未处于运行态，无法查看报表")
        page = s.get(PageSpec, page_id)
        if page is None or page.deployment_id != deployment_id or page.kind != "dashboard":
            raise KeyError("报表不存在")
        if role == "operator":
            granted = member_service.granted_menu_ids(s, d, uid)
            visible = any(
                m.ref_id == page_id and m.id in granted
                for m in s.query(DeploymentMenu).filter_by(deployment_id=deployment_id).all()
            )
            if not visible:
                raise member_service.PermissionDenied("该报表尚未授权给你")
        conn = s.get(Connection, d.connection_id) if d.connection_id else None
        dialect = conn.dialect if conn else "sqlite"
        title = page.title
        try:
            layout = json.loads(page.layout_json or "{}")
        except (ValueError, TypeError):
            layout = {}

    sql = layout.get("sql") or ""
    if not sql:
        raise QaError("报表 SQL 为空，无法运行")
    # 报表是只读聚合视图：白名单取该部署全部声明表（已在上方做菜单授权校验）；
    # 不用会话 registry，避免操作员的表级白名单把报表引用的表挡在门外。
    _d, registry, engine = crud_runtime.registry_for_service(app, deployment_id, uid)
    try:
        inner = enforce_readonly(sql, registry.allowed_tables, dialect)
        result = _execute(engine, inner, dialect)
    finally:
        engine.dispose()
    chart = layout.get("chart")
    return {
        "page_id": page_id,
        "title": title,
        "sql": inner,
        "chart": chart if isinstance(chart, dict) else None,
        "columns": result["columns"],
        "rows": result["rows"],
        "row_count": len(result["rows"]),
    }
