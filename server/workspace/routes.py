"""工作台运行时接口（M2-2）：取得部署的菜单树 + 页面规格，供前端渲染运行时。

- 菜单：deployment_menus（含 parent_id 树），ref_id → page_specs.id（闭环 B2）。
- 页面：page_specs 富化（解析出业务表名/表中名），前端按 kind 渲染。
- 访问：已登录（app.py 统一拦截）；成员/按人授权过滤在 M2-5 接入。
"""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify

from server.models import Deployment, DeploymentMenu, PageSpec, TableSpec

bp = Blueprint("workspace", __name__)


def _menu_tree(menus: list[DeploymentMenu]) -> list[dict]:
    """把平铺菜单（按 id 排序）构造成树：parent_id 指向本集合内的菜单。

    孤儿（parent 不在集合内）按根节点处理，避免丢失。
    """
    nodes = {
        m.id: {
            "id": m.id,
            "label": m.label,
            "kind": m.kind,
            "ref_id": m.ref_id,
            "children": [],
        }
        for m in menus
    }
    roots: list[dict] = []
    for m in menus:
        node = nodes[m.id]
        parent = nodes.get(m.parent_id)
        if m.parent_id and parent is not None:
            parent["children"].append(node)
        else:
            roots.append(node)
    return roots


@bp.get("/<int:deployment_id>")
def get_workspace(deployment_id: int):
    Session = current_app.extensions["datamason_session_factory"]
    with Session() as s:
        d = s.get(Deployment, deployment_id)
        if d is None:
            return jsonify({"error": "部署不存在"}), 404
        if d.status != "running":
            return jsonify({"error": f"该部署尚未就绪（状态：{d.status}），请回到控制台查看"}), 409
        menus = (
            s.query(DeploymentMenu)
            .filter_by(deployment_id=deployment_id)
            .order_by(DeploymentMenu.id)
            .all()
        )
        pages = (
            s.query(PageSpec)
            .filter_by(deployment_id=deployment_id)
            .order_by(PageSpec.id)
            .all()
        )
        tables = {
            t.id: t
            for t in s.query(TableSpec).filter_by(deployment_id=deployment_id).all()
        }
        payload = {
            "deployment_id": deployment_id,
            "biz_name": d.biz_name,
            "status": d.status,
            "entry_path": d.entry_path,
            "menus": _menu_tree(menus),
            "pages": [
                {
                    "id": p.id,
                    "kind": p.kind,
                    "access": p.access,
                    "title": p.title,
                    "table": tables[p.table_id].name
                    if (p.table_id and p.table_id in tables)
                    else None,
                    "table_biz_name": tables[p.table_id].biz_name
                    if (p.table_id and p.table_id in tables)
                    else None,
                }
                for p in pages
            ],
        }
    return jsonify(payload)