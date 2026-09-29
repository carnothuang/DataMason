"""M7-0 地基：元数据库三表与控件相容规格（0.13.0 / 需求规格 §6、技术方案 M7-0）。

覆盖：建表幂等、三表唯一约束、相容矩阵正反例、规格边界拒绝。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from datamason.forms.spec import (
    MAX_OPTIONS_PER_FIELD,
    FieldConfigSpec,
    FormConfigPayload,
    LayoutSpec,
    assert_control_compatible,
    default_control_type,
)


def test_create_all_lazy_idempotent(app):
    from datamason.models import create_all_lazy

    create_all_lazy(app)
    create_all_lazy(app)
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        from datamason.models import FormFieldConfig, FormLayout, FormOptionItem

        assert s.query(FormFieldConfig).count() == 0
        assert s.query(FormOptionItem).count() == 0
        assert s.query(FormLayout).count() == 0


def test_form_field_config_unique(app):
    from datamason.models import FormFieldConfig

    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        s.add(FormFieldConfig(deployment_id=1, table_name="t", field_name="f",
                              control_type="text"))
        s.commit()
    with Session() as s:
        s.add(FormFieldConfig(deployment_id=1, table_name="t", field_name="f",
                              control_type="radio"))
        with pytest.raises(IntegrityError):
            s.commit()


def test_form_option_item_unique(app):
    from datamason.models import FormOptionItem

    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        s.add(FormOptionItem(deployment_id=1, table_name="t", field_name="f",
                             value="a", label="A"))
        s.commit()
    with Session() as s:
        s.add(FormOptionItem(deployment_id=1, table_name="t", field_name="f",
                             value="a", label="B"))
        with pytest.raises(IntegrityError):
            s.commit()


def test_form_layout_unique(app):
    from datamason.models import FormLayout

    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        s.add(FormLayout(deployment_id=1, table_name="t", page_kind="workspace"))
        s.commit()
    with Session() as s:
        s.add(FormLayout(deployment_id=1, table_name="t", page_kind="workspace"))
        with pytest.raises(IntegrityError):
            s.commit()


@pytest.mark.parametrize("data_type,control", [
    ("FILE", "file"),
    ("VARCHAR", "text"),
    ("TEXT", "textarea"),
    ("INTEGER", "number"),
    ("DECIMAL", "number"),
    ("BOOLEAN", "boolean"),
    ("INTEGER", "radio"),
    ("VARCHAR", "checkbox"),
    ("DATETIME", "date"),
    ("DATETIME", "datetime"),
    ("VARCHAR", "date"),
    ("VARCHAR(255)", "text"),
])
def test_control_compatible_ok(data_type, control):
    assert_control_compatible(data_type, control)


@pytest.mark.parametrize("data_type,control", [
    ("INTEGER", "file"),
    ("VARCHAR", "file"),
    ("BOOLEAN", "number"),
    ("INTEGER", "textarea"),
    ("FILE", "text"),
    ("DATETIME", "checkbox"),
])
def test_control_compatible_reject(data_type, control):
    with pytest.raises(ValueError):
        assert_control_compatible(data_type, control)


def test_ref_control_requires_fk():
    with pytest.raises(ValueError):
        assert_control_compatible("INTEGER", "ref", is_fk=False)
    assert_control_compatible("INTEGER", "ref", is_fk=True)


def test_default_control_mapping():
    assert default_control_type("FILE") == "file"
    assert default_control_type("BOOLEAN") == "boolean"
    assert default_control_type("INTEGER") == "number"
    assert default_control_type("DATETIME") == "datetime"
    assert default_control_type("TEXT") == "textarea"
    assert default_control_type("VARCHAR") == "text"
    assert default_control_type("VARCHAR", is_fk=True) == "ref"


def test_field_config_rejects_duplicate_option_value():
    with pytest.raises(ValidationError):
        FieldConfigSpec(
            field_name="f", control_type="radio",
            options=[{"value": "a", "label": "A"}, {"value": "a", "label": "B"}],
        )


def test_field_config_rejects_empty_option_value():
    with pytest.raises(ValidationError):
        FieldConfigSpec(field_name="f", control_type="radio",
                        options=[{"value": "", "label": "A"}])


def test_field_config_rejects_too_many_options():
    options = [{"value": f"v{i}", "label": f"L{i}"} for i in range(MAX_OPTIONS_PER_FIELD + 1)]
    with pytest.raises(ValidationError):
        FieldConfigSpec(field_name="f", control_type="radio", options=options)


def test_field_config_rejects_unknown_control():
    with pytest.raises(ValidationError):
        FieldConfigSpec(field_name="f", control_type="unknown_control")


def test_field_config_rejects_long_hint():
    with pytest.raises(ValidationError):
        FieldConfigSpec(field_name="f", control_type="text", hint="x" * 201)


def test_field_config_rejects_granularity_without_time_control():
    with pytest.raises(ValidationError):
        FieldConfigSpec(field_name="f", control_type="text", time_granularity="date")


def test_layout_rejects_invalid_columns():
    with pytest.raises(ValidationError):
        LayoutSpec(columns_per_row=4)


def test_payload_rejects_duplicate_field():
    with pytest.raises(ValidationError):
        FormConfigPayload(fields=[
            {"field_name": "f", "control_type": "text"},
            {"field_name": "f", "control_type": "textarea"},
        ])


def test_payload_ok():
    payload = FormConfigPayload(
        fields=[{"field_name": "f", "control_type": "radio",
                 "options": [{"value": "a", "label": "A"}]}],
        layout={"columns_per_row": 2, "order": [{"field_name": "f"}],
                "groups": [{"title": "分组一", "field_names": ["f"]}]},
    )
    assert payload.layout is not None
    assert payload.layout.columns_per_row == 2