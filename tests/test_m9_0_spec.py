"""M9-0 控件规格扩展：五类新控件、控件参数、层级候选项、元数据库加列（0.15.0 / 技术方案 §二/§三）。

覆盖：相容矩阵双向口径（新控件正反例）、ControlConfigSpec 参数校验、参数与列类型交叉校验、
parent_value 悬空/成环/自引用拒绝与合法层级通过、模型加列与幂等回填。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import inspect as sa_inspect

from datamason.forms.spec import (
    PARAM_CONTROLS,
    ControlConfigSpec,
    FieldConfigSpec,
    FormConfigPayload,
    assert_control_compatible,
    assert_control_config_compatible,
    default_control_type,
)


# ---------- 5 类新控件相容矩阵 ----------

@pytest.mark.parametrize("data_type,control", [
    ("BOOLEAN", "switch"),
    ("INTEGER", "slider"),
    ("BIGINT", "slider"),
    ("DECIMAL", "slider"),
    ("INTEGER", "rate"),
    ("DECIMAL", "rate"),
    ("VARCHAR", "select"),
    ("INTEGER", "select"),
    ("BOOLEAN", "select"),
    ("TEXT", "select"),
    ("VARCHAR", "cascader"),
    ("TEXT", "cascader"),
])
def test_new_control_compatible_ok(data_type, control):
    assert_control_compatible(data_type, control)


@pytest.mark.parametrize("data_type,control", [
    ("INTEGER", "switch"),
    ("VARCHAR", "switch"),
    ("BOOLEAN", "slider"),
    ("VARCHAR", "slider"),
    ("BOOLEAN", "rate"),
    ("VARCHAR", "rate"),
    ("FILE", "select"),
    ("DATETIME", "select"),
    ("INTEGER", "cascader"),
    ("BOOLEAN", "cascader"),
    ("DATETIME", "cascader"),
])
def test_new_control_compatible_reject(data_type, control):
    with pytest.raises(ValueError):
        assert_control_compatible(data_type, control)


def test_default_control_mapping_unchanged():
    """零配置仍走旧默认，新控件不影响既有默认映射（零配置零变化）。"""
    assert default_control_type("BOOLEAN") == "boolean"
    assert default_control_type("INTEGER") == "number"
    assert default_control_type("VARCHAR") == "text"
    assert default_control_type("DECIMAL") == "number"


def test_param_controls_only_three():
    assert PARAM_CONTROLS == frozenset({"slider", "rate", "cascader"})


# ---------- ControlConfigSpec 参数自身校验 ----------

def test_control_config_ok_defaults():
    cfg = ControlConfigSpec()
    assert cfg.max_stars == 5
    assert cfg.allow_half is False
    assert cfg.expand_trigger == "click"


def test_control_config_rejects_min_gte_max():
    with pytest.raises(ValidationError):
        ControlConfigSpec(min=100, max=100)
    with pytest.raises(ValidationError):
        ControlConfigSpec(min=200, max=100)


def test_control_config_rejects_nonpositive_step():
    with pytest.raises(ValidationError):
        ControlConfigSpec(step=0)
    with pytest.raises(ValidationError):
        ControlConfigSpec(step=-1)


def test_control_config_rejects_stars_out_of_range():
    with pytest.raises(ValidationError):
        ControlConfigSpec(max_stars=0)
    with pytest.raises(ValidationError):
        ControlConfigSpec(max_stars=11)


def test_control_config_rejects_bad_expand_trigger():
    with pytest.raises(ValidationError):
        ControlConfigSpec(expand_trigger="dblclick")


# ---------- 参数与列类型交叉校验 ----------

def test_control_config_half_star_requires_decimal():
    # DECIMAL 允许半星
    assert_control_config_compatible(
        "DECIMAL", "rate", ControlConfigSpec(max_stars=5, allow_half=True))
    # 整数列半星拒绝
    with pytest.raises(ValueError):
        assert_control_config_compatible(
            "INTEGER", "rate", ControlConfigSpec(allow_half=True))


def test_control_config_integer_slider_step():
    # 整数列整数步长通过
    assert_control_config_compatible(
        "INTEGER", "slider", ControlConfigSpec(min=0, max=10, step=1))
    # 整数列小数步长拒绝
    with pytest.raises(ValueError):
        assert_control_config_compatible(
            "INTEGER", "slider", ControlConfigSpec(min=0, max=10, step=0.5))
    # DECIMAL 列允许小数步长
    assert_control_config_compatible(
        "DECIMAL", "slider", ControlConfigSpec(min=0, max=1, step=0.1))
    # 无参数直接放行
    assert_control_config_compatible("INTEGER", "slider", None)


# ---------- FieldConfigSpec.control_config 归属校验 ----------

def test_field_config_rejects_params_on_non_param_control():
    with pytest.raises(ValidationError):
        FieldConfigSpec(
            field_name="f", control_type="switch",
            control_config={"max_stars": 5},
        )


def test_field_config_ok_with_params_on_slider():
    fc = FieldConfigSpec(
        field_name="f", control_type="slider",
        control_config={"min": 0, "max": 100, "step": 10},
    )
    assert fc.control_config is not None
    assert fc.control_config.step == 10


# ---------- 层级候选项（parent_value）----------

def test_option_hierarchy_ok():
    fc = FieldConfigSpec(
        field_name="f", control_type="cascader",
        options=[
            {"value": "zj", "label": "浙江"},
            {"value": "hz", "label": "杭州", "parent_value": "zj"},
            {"value": "xh", "label": "西湖", "parent_value": "hz"},
        ],
    )
    assert len(fc.options) == 3


def test_option_hierarchy_rejects_dangling_parent():
    with pytest.raises(ValidationError):
        FieldConfigSpec(
            field_name="f", control_type="cascader",
            options=[
                {"value": "hz", "label": "杭州", "parent_value": "zj"},
            ],
        )


def test_option_hierarchy_rejects_self_reference():
    with pytest.raises(ValidationError):
        FieldConfigSpec(
            field_name="f", control_type="cascader",
            options=[
                {"value": "zj", "label": "浙江", "parent_value": "zj"},
            ],
        )


def test_option_hierarchy_rejects_cycle():
    with pytest.raises(ValidationError):
        FieldConfigSpec(
            field_name="f", control_type="cascader",
            options=[
                {"value": "a", "label": "A", "parent_value": "b"},
                {"value": "b", "label": "B", "parent_value": "a"},
            ],
        )


def test_option_hierarchy_rejects_deep_cycle():
    """三层自环也应被拒绝。"""
    with pytest.raises(ValidationError):
        FieldConfigSpec(
            field_name="f", control_type="cascader",
            options=[
                {"value": "a", "label": "A", "parent_value": "c"},
                {"value": "b", "label": "B", "parent_value": "a"},
                {"value": "c", "label": "C", "parent_value": "b"},
            ],
        )


# ---------- 模型加列与幂等回填 ----------

def test_new_columns_present_after_create(app):
    from datamason.models import create_all_lazy

    create_all_lazy(app)
    insp = sa_inspect(app.extensions["datamason_engine"])
    ffc_cols = {c["name"] for c in insp.get_columns("form_field_configs")}
    assert "control_config_json" in ffc_cols
    foi_cols = {c["name"] for c in insp.get_columns("form_option_items")}
    assert "parent_value" in foi_cols


def test_backfill_new_columns_idempotent(app):
    """已含新列时再次建表/补列不重复执行、不报错（幂等）。"""
    from datamason.models import create_all_lazy

    create_all_lazy(app)
    engine = app.extensions["datamason_engine"]
    create_all_lazy(app)
    from datamason.models import _backfill_missing_columns
    _backfill_missing_columns(engine)
    insp = sa_inspect(engine)
    assert "control_config_json" in {
        c["name"] for c in insp.get_columns("form_field_configs")
    }
    assert "parent_value" in {
        c["name"] for c in insp.get_columns("form_option_items")
    }


def test_model_columns_accept_new_values(app):
    from datamason.models import FormFieldConfig, FormOptionItem

    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        s.add(FormFieldConfig(
            deployment_id=1, table_name="t", field_name="f",
            control_type="slider", control_config_json='{"min":0,"max":100,"step":10}',
        ))
        s.add(FormOptionItem(
            deployment_id=1, table_name="t", field_name="f",
            value="hz", label="杭州", parent_value="zj",
        ))
        s.commit()
    with Session() as s:
        ffc = s.query(FormFieldConfig).filter_by(field_name="f").one()
        assert ffc.control_config_json == '{"min":0,"max":100,"step":10}'
        oi = s.query(FormOptionItem).filter_by(value="hz").one()
        assert oi.parent_value == "zj"


# ---------- 载荷层 ----------

def test_payload_ok_with_new_controls():
    payload = FormConfigPayload(fields=[
        {"field_name": "s", "control_type": "switch"},
        {"field_name": "sl", "control_type": "slider",
         "control_config": {"min": 0, "max": 100, "step": 10}},
        {"field_name": "c", "control_type": "cascader",
         "options": [
             {"value": "zj", "label": "浙江"},
             {"value": "hz", "label": "杭州", "parent_value": "zj"},
         ]},
    ])
    assert len(payload.fields) == 3
