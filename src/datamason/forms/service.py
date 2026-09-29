"""表单控件配置服务（0.13.0 M7-1/M7-2）：读 / 存 / 重置 / 写前校验回调。

设计要点：
- 配置与字段蓝图**解耦**：读取时与 `table_specs.columns_json` 求交，已删除字段的残留配置安全丢弃；
- 未配置字段按默认映射补齐（零配置零变化）；
- 任一步异常整体降级为默认（`degraded=True`）并记录含表名/字段名的可检索日志；
- 保存为**单事务删旧写新**，任一环节失败整体回滚、原配置保持有效；
- 本模块只读写元数据库，不含业务库 / HTTP / LLM 逻辑（纯领域）。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from pydantic import ValidationError

from datamason.forms import spec as form_spec
from datamason.models import (
    Deployment,
    FormFieldConfig,
    FormLayout,
    FormOptionItem,
    TableSpec,
)

logger = logging.getLogger(__name__)

DEFAULT_PAGE_KIND = "workspace"


class FormConfigError(ValueError):
    """表单配置校验/保存错误 → 路由层映射为 HTTP 400。"""


def _session(app):
    return app.extensions["datamason_session_factory"]()


def _warn(deployment_id: int, table_name: str, field_name: str | None, exc: Exception) -> None:
    """可检索降级日志：含部署 / 表名 / 字段名。"""
    logger.warning(
        "form_config degraded deployment=%s table=%s field=%s: %s",
        deployment_id, table_name, field_name or "-", exc,
    )


def _jloads(text: str, default):
    try:
        return json.loads(text) if text else default
    except (ValueError, TypeError):
        return default


def _first_error(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "配置格式错误"
    err = errors[0]
    loc = ".".join(str(x) for x in err.get("loc", ()))
    msg = err.get("message") or err.get("msg") or "配置格式错误"
    return f"{loc}：{msg}" if loc else msg


def _table_columns(app, deployment_id: int, table_name: str) -> list[dict]:
    """读取该部署该表的字段蓝图（columns_json）；不存在时抛 FormConfigError。"""
    with _session(app) as s:
        d = s.get(Deployment, deployment_id)
        if d is None:
            raise FormConfigError("部署不存在")
        ts = (
            s.query(TableSpec)
            .filter_by(deployment_id=deployment_id, name=table_name)
            .first()
        )
        if ts is None:
            raise FormConfigError("数据表不存在")
        return list(ts.columns)


def _layout_out(layout_row: FormLayout | None, by_name: dict[str, dict]) -> dict:
    if layout_row is None:
        return {"columns_per_row": 1, "order": [], "groups": []}
    order = [
        e.get("field_name")
        for e in _jloads(layout_row.order_json, [])
        if isinstance(e, dict) and e.get("field_name") in by_name
    ]
    groups = []
    for g in _jloads(layout_row.groups_json, []):
        if not isinstance(g, dict):
            continue
        names = [n for n in (g.get("field_names") or []) if n in by_name]
        groups.append({"title": g.get("title", ""), "field_names": names})
    return {
        "columns_per_row": layout_row.columns_per_row or 1,
        "order": order,
        "groups": groups,
    }


def _check_integrity(field_rows: list[FormFieldConfig],
                     option_rows: list[FormOptionItem]) -> None:
    """配置完整性检查：非法控件类型 / 候选项重复视为损坏（触发整体降级）。"""
    for r in field_rows:
        if r.control_type and r.control_type not in form_spec.CONTROL_TYPES:
            raise ValueError(f"非法控件类型：{r.control_type}")
    seen: set[tuple[str, str, str]] = set()
    for o in option_rows:
        key = (o.table_name, o.field_name, o.value)
        if key in seen:
            raise ValueError(f"候选项存储值重复：{o.field_name}.{o.value}")
        seen.add(key)


def _assemble(
    deployment_id: int,
    table_name: str,
    page_kind: str,
    columns: list[dict],
    field_rows: list[FormFieldConfig],
    option_rows: list[FormOptionItem],
    layout_row: FormLayout | None,
    *,
    degraded: bool = False,
) -> dict:
    """把三表配置与字段蓝图求交、补齐默认映射后组装为响应结构。"""
    by_name = {c["name"]: c for c in columns}
    cfg_by = {r.field_name: r for r in field_rows if r.field_name in by_name}
    opts_by: dict[str, list[dict]] = {}
    for o in option_rows:
        if o.field_name in by_name:
            item = {"value": o.value, "label": o.label}
            if o.parent_value is not None:
                item["parent_value"] = o.parent_value
            opts_by.setdefault(o.field_name, []).append(item)

    fields: list[dict] = []
    options_map: dict[str, list[dict]] = {}
    for c in columns:
        name = c["name"]
        cfg = cfg_by.get(name)
        data_type = c.get("type") or ""
        is_fk = bool(c.get("is_fk"))
        control = (
            cfg.control_type
            if cfg is not None and cfg.control_type
            else form_spec.default_control_type(data_type, is_fk=is_fk)
        )
        granularity = None
        if control == "datetime":
            granularity = (cfg.time_granularity if cfg else None) or "datetime"
        elif control == "date":
            granularity = (cfg.time_granularity if cfg else None) or "date"
        control_config = {}
        if cfg is not None and cfg.control_config_json:
            try:
                parsed = json.loads(cfg.control_config_json)
                if isinstance(parsed, dict):
                    control_config = parsed
            except (ValueError, TypeError):
                control_config = {}
        options = opts_by.get(name, [])
        fields.append({
            "field_name": name,
            "biz_name": c.get("biz_name") or name,
            "data_type": data_type,
            "length": c.get("length"),
            "nullable": bool(c.get("nullable", True)),
            "is_pk": bool(c.get("is_pk")),
            "is_ownership": bool(c.get("is_ownership")),
            "is_fk": is_fk,
            "ref_table": c.get("ref_table"),
            "control_type": control,
            "control_config": control_config,
            "time_granularity": granularity,
            "readonly": bool(cfg.readonly) if cfg else False,
            "hidden": bool(cfg.hidden) if cfg else False,
            "hint": cfg.hint if cfg else "",
            "options": options,
            "configured": cfg is not None,
        })
        if options:
            options_map[name] = options

    return {
        "deployment_id": deployment_id,
        "table": table_name,
        "page_kind": page_kind,
        "fields": fields,
        "options": options_map,
        "layout": _layout_out(layout_row, by_name),
        "degraded": degraded,
    }


def get_form_config(app, deployment_id: int, table_name: str, *,
                    page_kind: str = DEFAULT_PAGE_KIND) -> dict:
    """读取表单配置：求交 + 缺省补齐；任一步异常整体降级（degraded=True）不报错。"""
    try:
        columns = _table_columns(app, deployment_id, table_name)
    except FormConfigError:
        raise
    except Exception as exc:  # noqa: BLE001 读蓝图异常 → 降级为空蓝图
        _warn(deployment_id, table_name, None, exc)
        return _assemble(deployment_id, table_name, page_kind, [], [], [], None,
                         degraded=True)

    try:
        with _session(app) as s:
            field_rows = (
                s.query(FormFieldConfig)
                .filter_by(deployment_id=deployment_id, table_name=table_name)
                .all()
            )
            option_rows = (
                s.query(FormOptionItem)
                .filter_by(deployment_id=deployment_id, table_name=table_name)
                .order_by(FormOptionItem.order_index, FormOptionItem.id)
                .all()
            )
            layout_row = (
                s.query(FormLayout)
                .filter_by(deployment_id=deployment_id, table_name=table_name,
                           page_kind=page_kind)
                .first()
            )
            _check_integrity(field_rows, option_rows)
    except Exception as exc:  # noqa: BLE001 读配置异常 → 整体降级
        _warn(deployment_id, table_name, None, exc)
        return _assemble(deployment_id, table_name, page_kind, columns, [], [], None,
                         degraded=True)

    return _assemble(deployment_id, table_name, page_kind, columns,
                     field_rows, option_rows, layout_row)


def _field_row(deployment_id: int, table_name: str, f: form_spec.FieldConfigSpec,
               actor_user_id: int) -> FormFieldConfig:
    control_config = (
        f.control_config.model_dump(exclude_none=True)
        if f.control_config is not None else {}
    )
    return FormFieldConfig(
        deployment_id=deployment_id,
        table_name=table_name,
        field_name=f.field_name,
        control_type=f.control_type,
        control_config_json=json.dumps(control_config, ensure_ascii=False),
        time_granularity=f.time_granularity,
        readonly=f.readonly,
        hidden=f.hidden,
        hint=f.hint,
        updated_by=actor_user_id,
        updated_at=datetime.now(timezone.utc),
    )


def _validate_spec_against_blueprint(spec: form_spec.FormConfigPayload,
                                     by_name: dict[str, dict]) -> None:
    for f in spec.fields:
        col = by_name.get(f.field_name)
        if col is None:
            raise FormConfigError(f"字段不存在：{f.field_name}")
        label = col.get("biz_name") or f.field_name
        if col.get("is_pk") or col.get("is_ownership"):
            raise FormConfigError(f"字段「{label}」是主键/系统列，不可配置")
        if f.control_type is not None:
            try:
                form_spec.assert_control_compatible(
                    col.get("type") or "", f.control_type, is_fk=bool(col.get("is_fk"))
                )
            except ValueError as exc:
                raise FormConfigError(f"字段「{label}」：{exc}") from exc
        if f.control_config is not None:
            try:
                form_spec.assert_control_config_compatible(
                    col.get("type") or "", f.control_type, f.control_config
                )
            except ValueError as exc:
                raise FormConfigError(f"字段「{label}」：{exc}") from exc
        if f.control_type in ("radio", "checkbox") and f.options:
            if f.control_type == "radio" and len(f.options) < 1:
                raise FormConfigError(f"字段「{label}」：单选至少需要一个候选项")
        if f.control_type in ("date", "datetime") and f.time_granularity is None:
            # 允许缺省（前端会补），此处不强制
            pass


def save_form_config(app, deployment_id: int, table_name: str, payload: dict, *,
                     page_kind: str = DEFAULT_PAGE_KIND,
                     actor_user_id: int = 0) -> dict:
    """保存配置：校验 → 单事务删旧写新 → 审计；失败整体回滚，原配置保持有效。"""
    columns = _table_columns(app, deployment_id, table_name)
    by_name = {c["name"]: c for c in columns}
    if not isinstance(payload, dict):
        raise FormConfigError("配置格式错误")
    try:
        spec = form_spec.FormConfigPayload.model_validate(payload)
    except ValidationError as exc:
        raise FormConfigError(_first_error(exc)) from exc

    _validate_spec_against_blueprint(spec, by_name)

    layout = spec.layout
    order_entries = []
    if layout is not None:
        for e in layout.order:
            if e.field_name in by_name:
                order_entries.append({"field_name": e.field_name})
        groups = [
            {"title": g.title,
             "field_names": [n for n in g.field_names if n in by_name]}
            for g in layout.groups
        ]
    else:
        groups = []

    total_options = sum(len(f.options) for f in spec.fields)
    with _session(app) as s:
        try:
            s.query(FormFieldConfig).filter_by(
                deployment_id=deployment_id, table_name=table_name
            ).delete(synchronize_session=False)
            s.query(FormOptionItem).filter_by(
                deployment_id=deployment_id, table_name=table_name
            ).delete(synchronize_session=False)
            s.query(FormLayout).filter_by(
                deployment_id=deployment_id, table_name=table_name, page_kind=page_kind
            ).delete(synchronize_session=False)
            for f in spec.fields:
                if (f.control_type is None and not f.readonly and not f.hidden
                        and not f.hint and not f.options):
                    continue  # 全默认不落行（保持"未配置"语义）
                s.add(_field_row(deployment_id, table_name, f, actor_user_id))
                for i, o in enumerate(f.options):
                    s.add(FormOptionItem(
                        deployment_id=deployment_id,
                        table_name=table_name,
                        field_name=f.field_name,
                        value=o.value,
                        label=o.label,
                        parent_value=o.parent_value,
                        order_index=i,
                    ))
            if layout is not None:
                s.add(FormLayout(
                    deployment_id=deployment_id,
                    table_name=table_name,
                    page_kind=page_kind,
                    columns_per_row=layout.columns_per_row,
                    order_json=json.dumps(order_entries, ensure_ascii=False),
                    groups_json=json.dumps(groups, ensure_ascii=False),
                    updated_by=actor_user_id,
                    updated_at=datetime.now(timezone.utc),
                ))
            s.commit()
        except Exception as exc:  # noqa: BLE001 任一环节失败整体回滚
            s.rollback()
            _warn(deployment_id, table_name, None, exc)
            if isinstance(exc, FormConfigError):
                raise
            raise FormConfigError(f"保存表单配置失败：{exc}") from exc

    _audit(app, deployment_id, actor_user_id, table_name, "保存表单配置", {
        "page_kind": page_kind,
        "fields": [f.field_name for f in spec.fields],
        "options_count": total_options,
        "columns_per_row": layout.columns_per_row if layout else 1,
    })
    return {
        "saved": True,
        "deployment_id": deployment_id,
        "table": table_name,
        "page_kind": page_kind,
        "fields": len(spec.fields),
        "options": total_options,
        "has_layout": layout is not None,
    }


def reset_field_config(app, deployment_id: int, table_name: str, field_name: str, *,
                       actor_user_id: int = 0) -> dict:
    """恢复字段默认：删除该字段配置行与候选项行，写审计。"""
    columns = _table_columns(app, deployment_id, table_name)
    by_name = {c["name"]: c for c in columns}
    if field_name not in by_name:
        raise FormConfigError(f"字段不存在：{field_name}")
    with _session(app) as s:
        s.query(FormFieldConfig).filter_by(
            deployment_id=deployment_id, table_name=table_name, field_name=field_name
        ).delete(synchronize_session=False)
        s.query(FormOptionItem).filter_by(
            deployment_id=deployment_id, table_name=table_name, field_name=field_name
        ).delete(synchronize_session=False)
        s.commit()
    _audit(app, deployment_id, actor_user_id, table_name, "恢复默认", {
        "field": field_name,
    })
    return {"reset": True, "table": table_name, "field_name": field_name}


def _audit(app, deployment_id: int, actor_user_id: int, table_name: str,
           summary: str, changes: dict) -> None:
    """表单配置审计（元数据库单事务，失败不影响主流程）。"""
    from datamason.audit import service as audit_service

    try:
        audit_service.log_system(
            app, deployment_id, "form_config",
            actor_user_id=actor_user_id,
            object_kind="form_config",
            object_table=table_name,
            object_label=summary,
            changes=changes,
        )
    except Exception as exc:  # noqa: BLE001 审计旁路失败不阻断
        _warn(deployment_id, table_name, None, exc)

def _validate_value(field: dict, values: dict, name: str) -> None:
    """按字段配置校验单个提交值；可原地规整 values（多选数组 → JSON 串）。"""
    control = field.get("control_type")
    label = field.get("biz_name") or name
    raw = values.get(name)

    if control in ("radio", "checkbox"):
        options = [o["value"] for o in field.get("options") or []]
        if not options:
            return  # 未配置候选项 → 降级为文本输入，不校验
        if control == "radio":
            if raw is None or raw == "":
                return
            if str(raw) not in options:
                raise ValueError(f"「{label}」的值不在候选项范围内")
            return
        if raw is None or raw == "":
            values[name] = ""
            return
        items = raw
        if isinstance(items, str):
            try:
                items = json.loads(items)
            except (ValueError, TypeError) as exc:
                raise ValueError(f"「{label}」需要多选值数组") from exc
        if not isinstance(items, list):
            raise ValueError(f"「{label}」需要多选值数组")
        for item in items:
            if str(item) not in options:
                raise ValueError(f"「{label}」包含非法候选项：{item}")
        serialized = json.dumps([str(i) for i in items], ensure_ascii=False)
        length = field.get("length")
        if length and len(serialized) > int(length):
            raise ValueError(f"「{label}」多选值超出字段长度上限")
        values[name] = serialized
    elif control in ("date", "datetime"):
        if raw is None or raw == "" or isinstance(raw, datetime):
            return
        try:
            datetime.fromisoformat(str(raw).replace("Z", ""))
        except ValueError as exc:
            raise ValueError(f"「{label}」日期格式不正确") from exc
    elif control == "switch":
        # 收到的是 _coerce_value 规整后的值：BOOLEAN 列在 SQLite 反射为 INTEGER 时
        # 是 int 1/0，PG/MySQL 上是 bool；用 `raw == 0 or raw == 1` 兼容两种形态
        if raw is None or raw == "":
            return
        if not (raw == 0 or raw == 1):
            raise ValueError(f"「{label}」的值只能是 1 或 0")
    elif control in ("slider", "rate"):
        _validate_numeric_control(field, values, name, label, raw)
    elif control == "select":
        options = [o["value"] for o in field.get("options") or []]
        if not options:
            return  # 未配置候选项 → 降级为文本输入，不校验
        if raw is None or raw == "":
            return
        if str(raw) not in options:
            raise ValueError(f"「{label}」的值不在候选项范围内")
    elif control == "cascader":
        options = field.get("options") or []
        if not options:
            return
        if raw is None or raw == "":
            return
        leaf_values = _cascader_leaf_values(options)
        if str(raw) not in leaf_values:
            raise ValueError(f"「{label}」须选择末级选项")


def _cascader_leaf_values(options: list[dict]) -> set[str]:
    """级联候选项中可作为落库值的末级集合（有子项的父节点不可作为落库值）。"""
    all_values = {o["value"] for o in options}
    parent_values = {
        o["parent_value"] for o in options if o.get("parent_value") is not None
    }
    return all_values - parent_values


def _validate_numeric_control(field: dict, values: dict, name: str, label: str,
                              raw) -> None:
    """滑块/评分统一数值校验：范围、步长、整数列类型、整数列小数拒绝。"""
    if raw is None or raw == "":
        return
    try:
        num = float(raw)
    except (ValueError, TypeError):
        raise ValueError(f"「{label}」需要是数字") from None

    control = field.get("control_type")
    config = field.get("control_config") or {}
    data_type = (field.get("data_type") or "").upper()

    if control == "slider":
        if config.get("min") is not None and num < float(config["min"]):
            raise ValueError(f"「{label}」不能小于 {config['min']}")
        if config.get("max") is not None and num > float(config["max"]):
            raise ValueError(f"「{label}」不能大于 {config['max']}")
        step = config.get("step")
        if step is not None and float(step) > 0:
            base = float(config.get("min") or 0)
            span = (num - base) / float(step)
            if abs(span - round(span)) > 1e-6:
                raise ValueError(f"「{label}」须为 {step} 的整数倍")
        # 整数列拒绝小数
        if data_type in ("INTEGER", "BIGINT") and num != int(num):
            raise ValueError(f"「{label}」必须是整数")
    elif control == "rate":
        max_stars = int(config.get("max_stars") or 5)
        if num < 1 or num > max_stars:
            raise ValueError(f"「{label}」评分须在 1~{max_stars} 之间")
        allow_half = bool(config.get("allow_half"))
        if allow_half:
            if abs(num * 2 - round(num * 2)) > 1e-6:
                raise ValueError(f"「{label}」评分只支持半星")
        elif num != int(num):
            raise ValueError(f"「{label}」评分必须是整数")


def build_validator(app, deployment_id: int, table_name: str, *,
                    page_kind: str = DEFAULT_PAGE_KIND):
    """构造写前校验回调 `(table_name, values) -> None`。

    闭包内按需读取该表配置（一次读取缓存于闭包），对提交载荷逐字段校验并规整；
    配置不可用（部署/表不存在）时静默放行，保证"未配置零变化"。
    """

    def _validate(table: str, values: dict) -> None:
        if table != table_name:
            return
        try:
            cfg = get_form_config(app, deployment_id, table_name, page_kind=page_kind)
        except FormConfigError:
            return
        fields = {f["field_name"]: f for f in cfg["fields"]}
        for name in list(values.keys()):
            field = fields.get(name)
            if field is not None:
                _validate_value(field, values, name)

    return _validate


def build_registry_validator(app, deployment_id: int, table_names: list[str]):
    """为多表 registry 构造统一校验回调：按表名分发到 `build_validator`。"""
    cache: dict[str, object] = {}

    def _validate(table: str, values: dict) -> None:
        validator = cache.get(table)
        if validator is None:
            validator = build_validator(app, deployment_id, table)
            cache[table] = validator
        validator(table, values)  # type: ignore[operator]

    return _validate


def generate_option_items(app, *, field_label: str, description: str,
                          deployment_id: int = 0,
                          max_items: int = form_spec.MAX_OPTIONS_PER_FIELD) -> dict:
    """NL → 候选项候选清单（可选能力，M7-7）：确定性去重/裁剪，仅返回清单不写配置。

    - 未配置默认模型 → FormConfigError（400）；
    - LLM 调用失败 → LlmCallError（路由映射 502），保留原配置不变；
    - 成功/失败均写 `ai_generations`（kind="options"）埋点。
    """
    from datamason.ai import service as ai_service
    from datamason.llm import structure as llm_structure
    from datamason.settings.service import get_default_llm_client

    prompt_summary = f"{field_label}：{description}"[:200]
    client = get_default_llm_client(app)
    if client is None:
        ai_service.record(app, "options", "error", prompt_summary=prompt_summary,
                          result_summary="未配置默认模型", deployment_id=deployment_id)
        raise FormConfigError("尚未配置默认模型，请到设置页配置后再试")

    try:
        items, meta = llm_structure.generate_option_items(
            client, field_label=field_label, description=description,
            max_items=max_items)
    except Exception as exc:  # noqa: BLE001 LLM 失败 → 埋点后上抛（路由 502）
        ai_service.record(app, "options", "error", prompt_summary=prompt_summary,
                          result_summary=str(exc), deployment_id=deployment_id)
        raise

    seen: set[str] = set()
    cleaned: list[dict] = []
    for it in items:
        value = str(it.get("value") or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        cleaned.append({"value": value, "label": str(it.get("label") or value).strip()[:100]})
        if len(cleaned) >= max_items:
            break
    ai_service.record(app, "options", "ok", prompt_summary=prompt_summary,
                      result_summary=f"生成 {len(cleaned)} 项候选项",
                      usage=meta.get("usage"), deployment_id=deployment_id)
    return {"items": cleaned, "attempts": int(meta.get("attempts", 1))}