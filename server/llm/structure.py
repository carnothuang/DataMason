"""NL→结构：一句话 → table_spec（设计细化 §十一·四 ①②）。

- LLM 只产 table_spec JSON；pydantic 强校验；失败用中文原因回喂重试（上限 2 次）。
- 确定性种子数据生成器（DR-08，M0/M1 不用 LLM）：每表 8~12 行，
  外键值取自被引用表已生成行，内置语料取值，零成本可复现。
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError

from server.ddl.table_spec import SchemaSpec
from server.llm.client import LlmCallError, OpenAiCompatibleClient

MAX_RETRY = 2

SYSTEM_PROMPT = """你是数据库设计专家。根据用户的一句话业务描述，设计一套关系型数据库结构。
只输出一个 JSON 对象，不要输出任何解释或 markdown 代码块标记。结构必须严格符合：
{
  "biz_notes": "整库是做什么的（一句话）",
  "tables": [
    {
      "name": "snake_case 英文表名",
      "biz_name": "中文业务名",
      "biz_notes": "这张表是干什么的",
      "columns": [
        {
          "name": "snake_case 英文列名",
          "biz_name": "中文列名",
          "type": "INTEGER|BIGINT|VARCHAR|TEXT|BOOLEAN|DATETIME|DECIMAL 之一",
          "length": 255,
          "nullable": true,
          "is_pk": false,
          "autoincrement": false,
          "is_fk": false,
          "ref_table": null,
          "ref_column": null,
          "default": null
        }
      ],
      "indexes": [["column_name"]]
    }
  ]
}
硬性规则：
1. 每张表必须且只能有一个主键列（通常 id，INTEGER，is_pk=true, autoincrement=true）；
2. 表间关联用外键列表达：is_fk=true，ref_table 与 ref_column 必须指向已存在表的主键；
3. 列类型只能取给定白名单；VARCHAR 必须给 length；
4. 不要生成 created_by/created_at/updated_by/updated_at（系统自动注入归属列）；
   业务上确实需要"提交人/提交时间/回复时间"时，请用其他列名，
   例如 submitter_id、submit_time、replied_at、occurred_time；
5. 表名、列名只用小写字母、数字、下划线，且以字母开头；
6. 验证类小系统通常 2~5 张表即可，保持精简、规范。"""


def generate_schema(client: OpenAiCompatibleClient, description: str) -> tuple[SchemaSpec, dict]:
    """一句话 → 合法 SchemaSpec。失败回喂中文错误重试，超过上限抛出。

    返回 (schema, meta)，meta 含重试次数与用量。
    """
    user_prompt = f"业务描述：{description}\n请输出 table_spec JSON。"
    last_error = ""
    attempts = 0

    for attempt in range(MAX_RETRY + 1):
        attempts = attempt + 1
        if attempt == 0:
            prompt = user_prompt
        else:
            prompt = (
                f"{user_prompt}\n\n上一次生成的结构不合法：{last_error}\n"
                "请修正后重新输出完整 JSON。"
            )
        parsed, _usage = client.chat_json(SYSTEM_PROMPT, prompt)
        try:
            normalized = _normalize_ownership_columns(parsed)
            schema = SchemaSpec.model_validate(normalized)
            return schema, {"attempts": attempts}
        except ValidationError as exc:
            last_error = _flatten_validation_error(exc)
    raise LlmCallError(f"经过 {attempts} 次生成仍不合法：{last_error}")


# 系统会注入的审计归属列（DR-05）。真实模型高频自行生成这些列，
# 其中纯审计列在解析前静默剔除（由系统注入同名列接管）；
# 仅当 created_by/updated_by 被当作指向业务表的外键（真实业务语义，如"经办人"）时保留，
# 交给强校验拦截并要求 LLM 改名重试。
_PURE_AUDIT_COLUMNS = {"created_at", "updated_at"}
_ACTOR_COLUMNS = {"created_by", "updated_by"}


def _normalize_ownership_columns(parsed: object) -> object:
    if not isinstance(parsed, dict):
        return parsed
    for table in parsed.get("tables", []) or []:
        cols = table.get("columns") or []
        kept = []
        for col in cols:
            if not isinstance(col, dict):
                kept.append(col)
                continue
            name = str(col.get("name", "")).lower()
            is_fk = bool(col.get("is_fk"))
            if name in _PURE_AUDIT_COLUMNS and not is_fk:
                continue  # 静默剔除，系统注入
            if name in _ACTOR_COLUMNS and not is_fk:
                continue  # 非外键的 created_by 也是纯审计列，剔除
            kept.append(col)
        table["columns"] = kept
    return parsed


def _flatten_validation_error(exc: ValidationError) -> str:
    msgs = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"])
        msgs.append(f"{loc}: {err['msg']}")
    return "；".join(msgs[:5])


# ---------- 确定性演示种子数据（DR-08，不经 LLM） ----------

_NAME_POOL = ["老猫", "王姐", "小李", "小张", "陈总", "阿强", "美琳", "子轩", "雨桐", "建国"]
_WORD_POOL = ["示例", "验证", "演示", "测试", "常规", "重点", "临时", "跟进", "已处理", "待跟进"]


def _seed_value(col, table_biz, ref_pk_map, rng):
    name = col.name.lower()
    # 主键自增：不显式给值
    if col.is_pk and col.autoincrement:
        return None
    # 外键：从被引用表已生成主键中取
    if col.is_fk and col.ref_table in ref_pk_map and ref_pk_map[col.ref_table]:
        return rng.choice(ref_pk_map[col.ref_table])
    # 归属列（创建模式强制）：系统首用户 id=1
    if name in ("created_by", "updated_by"):
        return 1
    if name in ("created_at", "updated_at"):
        return (datetime.now(timezone.utc) - timedelta(days=rng.randint(0, 20))).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    # 常见语义列
    if "name" in name or name in ("visitor", "person", "owner"):
        return rng.choice(_NAME_POOL)
    if col.type == "BOOLEAN":
        return rng.choice([0, 1])
    if col.type in ("INTEGER", "BIGINT"):
        return rng.randint(1, 100)
    if col.type == "DECIMAL":
        return round(rng.uniform(1, 999), 2)
    if col.type == "DATETIME":
        return (datetime.now(timezone.utc) - timedelta(days=rng.randint(0, 20))).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    if col.length is not None and col.length <= 8:
        return f"{table_biz[:2]}{rng.randint(10, 99)}"
    return f"{rng.choice(_WORD_POOL)}{table_biz}{rng.randint(1, 99)}"


# ---------- 直连模式：反向生成结构后 AI 补业务标签（M2-7，可选） ----------

ENRICH_SYSTEM_PROMPT = """你是数据库设计专家。下面给出用户的部署目标，以及从现有数据库反向生成的表结构（表名/列名/主外键）。
请只输出一个 JSON 对象（不要任何解释或 markdown 代码块标记），补充中文业务名：
{
  "biz_notes": "这套系统是做什么的（一句话），基于目标与已有库结构",
  "tables": [
    {"name": "表名（原样保留），", "biz_name": "这张表的中文业务名"}
  ]
}
硬性规则：
1. tables 列表必须与原结构一一对应，不允许增删表；name 字段原样保留；
2. biz_name 用通俗中文业务名（如 客户表/订单表）；
3. 不要输出表结构、列定义等内容。"""


def enrich_labels(
    client: OpenAiCompatibleClient,
    purpose: str,
    schema_dict: dict,
) -> tuple[SchemaSpec, dict]:
    """反向生成的 spec + 部署目标 → 补中文业务名/biz_notes（可选增强）。

    只改动 biz_name/biz_notes，结构字段原样保留并重新过 pydantic 强校验；
    失败抛 LlmCallError，由直连预览层回退为确定性默认（表名即业务名）。
    """
    import json

    from pydantic import ValidationError

    user_prompt = (
        f"部署目标：{purpose}\n"
        "反向生成的结构：\n"
        f"{json.dumps(schema_dict, ensure_ascii=False)}\n"
        "请补充每张表的中文业务名。"
    )
    parsed, _usage = client.chat_json(ENRICH_SYSTEM_PROMPT, user_prompt)
    if not isinstance(parsed, dict):
        raise LlmCallError("补标签响应不是 JSON 对象")
    tables = parsed.get("tables")
    if not isinstance(tables, list):
        raise LlmCallError("补标签响应缺少 tables 列表")

    enriched = dict(schema_dict)
    enriched["biz_notes"] = str(parsed.get("biz_notes") or schema_dict.get("biz_notes") or "")
    by_name = {t["name"]: t for t in tables if isinstance(t, dict) and t.get("name")}
    out_tables = []
    for t in schema_dict.get("tables", []):
        t = dict(t) if isinstance(t, dict) else t
        label = by_name.get(t["name"], {}).get("biz_name")
        t["biz_name"] = str(label).strip() if label else t.get("biz_name", "")
        out_tables.append(t)
    enriched["tables"] = out_tables
    try:
        schema = SchemaSpec.model_validate(enriched)
    except ValidationError as exc:
        raise LlmCallError(f"补标签后结构不合法：{_flatten_validation_error(exc)}") from exc
    return schema, {"attempts": 1}


def generate_seed_rows(spec: SchemaSpec, rows_per_table: int = 10, seed: int = 42) -> dict[str, list[dict]]:
    """按外键拓扑序为每表生成确定性数据。返回 {table_name: [row, ...]}。

    可复现：固定 seed + 固定取值顺序，不调用 LLM。
    """
    rng = random.Random(seed)
    from server.ddl.ddl_builder import _topo_sort, inject_ownership_columns  # 拓扑序保证外键先有行

    # 与 DDL 落库保持一致：种子基于注入归属列后的表
    ordered = [inject_ownership_columns(t) for t in _topo_sort(spec)]
    rows: dict[str, list[dict]] = {}
    ref_pk_map: dict[str, list[int]] = {}

    for table in ordered:
        table_rows = []
        pk_name = table.pk_columns[0].name
        generated_pks = []
        for i in range(rows_per_table):
            row = {}
            pk_value = i + 1
            for col in table.columns:
                value = _seed_value(col, table.biz_name or table.name, ref_pk_map, rng)
                if value is None:
                    continue
                row[col.name] = value
            # 自增主键显式编号，便于外键引用
            row[pk_name] = pk_value
            generated_pks.append(pk_value)
            table_rows.append(row)
        rows[table.name] = table_rows
        ref_pk_map[table.name] = generated_pks
    return rows
