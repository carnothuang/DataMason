"""M0-4 验收：反射 CRUD 引擎对 1 张真实表的全操作（SQLite）。"""
from __future__ import annotations

import pytest
from flask import Flask

from datamason.crud.engine import CrudRegistry, create_crud_blueprint
from datamason.dataaccess.manager import execute_ddl, make_engine
from datamason.ddl.ddl_builder import build_table_ddl
from datamason.ddl.table_spec import ColumnSpec, TableSpecModel


@pytest.fixture()
def crud_client(tmp_path):
    engine = make_engine("sqlite", str(tmp_path / "crud.db"))
    table = TableSpecModel(
        name="messages",
        biz_name="留言",
        columns=[
            ColumnSpec(name="id", type="INTEGER", is_pk=True, autoincrement=True,
                       nullable=False),
            ColumnSpec(name="visitor", type="VARCHAR", length=64, nullable=False),
            ColumnSpec(name="content", type="TEXT"),
            ColumnSpec(name="is_open", type="BOOLEAN", default="1"),
        ],
    )
    execute_ddl(engine, [build_table_ddl(table, "sqlite")])

    registry = CrudRegistry(engine, tables=["messages"])
    app = Flask(__name__)
    app.register_blueprint(create_crud_blueprint(registry, "/api/v1/demo"))
    app.testing = True
    return app.test_client()


def test_meta_reflects_columns(crud_client):
    data = crud_client.get("/api/v1/demo/meta").get_json()
    assert "messages" in data
    names = {c["name"] for c in data["messages"]["columns"]}
    assert {"id", "visitor", "content", "is_open"} <= names


def test_crud_lifecycle(crud_client):
    # CREATE
    resp = crud_client.post(
        "/api/v1/demo/tables/messages/rows",
        json={"visitor": "老猫", "content": "第一条留言", "is_open": True},
    )
    assert resp.status_code == 201
    row_id = resp.get_json()["id"]
    assert row_id == 1

    # READ one
    one = crud_client.get(f"/api/v1/demo/tables/messages/rows/{row_id}").get_json()
    assert one["visitor"] == "老猫"
    assert one["content"] == "第一条留言"

    # UPDATE
    upd = crud_client.put(
        f"/api/v1/demo/tables/messages/rows/{row_id}",
        json={"content": "改过的留言"},
    )
    assert upd.get_json()["updated"] is True
    one2 = crud_client.get(f"/api/v1/demo/tables/messages/rows/{row_id}").get_json()
    assert one2["content"] == "改过的留言"
    assert one2["visitor"] == "老猫"

    # DELETE
    dele = crud_client.delete(f"/api/v1/demo/tables/messages/rows/{row_id}")
    assert dele.get_json()["deleted"] is True
    assert crud_client.get(f"/api/v1/demo/tables/messages/rows/{row_id}").status_code == 404


def test_list_pagination_filter_sort(crud_client):
    for i in range(5):
        crud_client.post(
            "/api/v1/demo/tables/messages/rows",
            json={"visitor": f"user{i % 2}", "content": f"m{i}"},
        )

    # 过滤（参数化等值）
    resp = crud_client.get("/api/v1/demo/tables/messages/rows?visitor=user0")
    body = resp.get_json()
    assert body["total"] == 3
    assert all(r["visitor"] == "user0" for r in body["data"])

    # 分页
    page = crud_client.get("/api/v1/demo/tables/messages/rows?page=2&page_size=2").get_json()
    assert page["total"] == 5
    assert len(page["data"]) == 2

    # 排序（倒序）
    desc_rows = crud_client.get(
        "/api/v1/demo/tables/messages/rows?sort=id&order=desc&page_size=5"
    ).get_json()["data"]
    ids = [r["id"] for r in desc_rows]
    assert ids == sorted(ids, reverse=True)


def test_unknown_filter_column_rejected(crud_client):
    resp = crud_client.get("/api/v1/demo/tables/messages/rows?evil=1")
    assert resp.status_code == 400


def test_unknown_table_404(crud_client):
    assert crud_client.get("/api/v1/demo/tables/nope/rows").status_code == 404


def test_sql_injection_in_filter_is_parameterized(crud_client):
    crud_client.post("/api/v1/demo/tables/messages/rows",
                     json={"visitor": "keep", "content": "x"})
    # 注入串只作为绑定值，不会逃出列名/语句
    resp = crud_client.get(
        "/api/v1/demo/tables/messages/rows",
        query_string={"visitor": "x' OR '1'='1"},
    )
    assert resp.status_code == 200
    assert resp.get_json()["total"] == 0
