"""M5-0 地基契约测试（0.11.0）：元数据扩展 + 类型 + 配置。

覆盖计划 M5-0-1 / M5-0-2 的验收：
- `attachments` 表随惰性建表建齐
- `deployment_menu_grants` 具备 actions_json / data_scope（旧库回填亦可用）
- `ColumnType` 支持 FILE 且 DDL 确定性映射为 VARCHAR
- 上传/导入相关配置项与 Flask MAX_CONTENT_LENGTH 就位
"""
from __future__ import annotations

from sqlalchemy import inspect as sa_inspect

from server.ddl.ddl_builder import build_table_ddl
from server.ddl.table_spec import ColumnSpec, TableSpecModel
from server.models import Attachment, DeploymentMenuGrant


def test_attachments_table_created(app):
    Session = app.extensions["datamason_session_factory"]
    with Session() as s:
        s.query(Attachment).count()
    insp = sa_inspect(app.extensions["datamason_engine"])
    assert "attachments" in insp.get_table_names()
    cols = {c["name"] for c in insp.get_columns("attachments")}
    assert {"deployment_id", "table_name", "row_pk", "field_name",
            "token", "filename", "mime", "size", "storage_rel", "status",
            "uploaded_by"} <= cols


def test_menu_grant_has_action_and_scope_columns(app):
    insp = sa_inspect(app.extensions["datamason_engine"])
    cols = {c["name"] for c in insp.get_columns("deployment_menu_grants")}
    assert {"actions_json", "data_scope"} <= cols
    # 默认空 = 按角色默认（存量兼容）
    with app.extensions["datamason_session_factory"]() as s:
        g = DeploymentMenuGrant(menu_id=1, user_id=1)
        assert g.actions == {}
    assert DeploymentMenuGrant.__table__.c.data_scope.default.arg == ""


def test_file_column_type_maps_to_varchar():
    col = ColumnSpec(name="invoice", biz_name="发票", type="FILE")
    table = TableSpecModel(
        name="expense", biz_name="报销单",
        columns=[
            ColumnSpec(name="id", type="INTEGER", is_pk=True, autoincrement=True),
            col,
        ],
    )
    for dialect in ("sqlite", "mysql", "postgresql"):
        ddl = build_table_ddl(table, dialect, with_ownership=False)
        assert "invoice" in ddl
        assert "VARCHAR(255)" in ddl


def test_upload_and_import_config_present(app):
    assert app.config["MAX_CONTENT_LENGTH"] > 0
    assert app.config["DM_UPLOAD_MAX_FILE_BYTES"] > 0
    assert ".pdf" in app.config["DM_UPLOAD_ALLOWED_EXT"]
    assert app.config["DM_IMPORT_MAX_ROWS"] > 0
