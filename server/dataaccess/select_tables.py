"""_select_relevant_tables：直连模式两段式选表的能力契约（DR-18，自实现）。

移植清单：`_select_relevant_tables`（两段式选表）| server/dataaccess/ |
直连模式 schema 过大时的相关表筛选。

能力契约：
- 输入：全库表清单（表名 + 列名 + 直接引用的外键目标表）+ 用户一句话意图；
- 输出：按相关度排序的表名子集，确定性、无 LLM、有界（≤ SELECT_MAX_TABLES）；
- 语义：与意图关键词直接命中的表优先（名/列命中 ×2），外键引用链上一跳邻居 +1；
  全部零命中时回退"连接度优先"（被引用数最多的中心表排前），仍是确定性有序；
- 触发：由调用方在表数 > SELECT_TRIGGER 时启用两段式；对未触发场景同样可安全调用。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# 契约常量（前端展示/测试可引用）
SELECT_TRIGGER = 8      # 清单表数超过该值即进入两段式选表
SELECT_MAX_TABLES = 12  # 单轮返回上限（含引用链邻居）

_DIRECT_SCORE = 2
_NEIGHBOR_SCORE = 1


@dataclass(frozen=True)
class TableInventory:
    name: str
    columns: tuple[str, ...]
    fk_targets: tuple[str, ...]  # 本表直接引用的表名


def select_relevant_tables(
    inventory: list[TableInventory],
    purpose: str,
    max_tables: int = SELECT_MAX_TABLES,
) -> list[str]:
    """筛出与意图相关的表名（确定性排序）。

    - 关键词命中：意图中的英文词元（customer→customers 子串命中）落在表名/列名 → +2；
    - 引用链邻居：直接引用命中表，或被命中表直接引用 → +1；
    - 排序：分高在前；同分按表名 asc（确定性）；截断到 max_tables；
    - 全部零命中：回退"连接度优先"（被引次数 desc，再按表名 asc）。
    """
    if not inventory:
        return []
    names = [t.name for t in inventory]
    by_name = {t.name: t for t in inventory}
    referenced_by: dict[str, set[str]] = {n: set() for n in names}
    for t in inventory:
        for target in t.fk_targets:
            if target in by_name:
                referenced_by[target].add(t.name)

    tokens = _purpose_tokens(purpose)
    scores: dict[str, int] = {n: 0 for n in names}
    for t in inventory:
        haystack = [t.name] + list(t.columns)
        if any(token in name for name in haystack for token in tokens):
            scores[t.name] += _DIRECT_SCORE * len(tokens)
    # 引用链上一跳：命中表的邻居 +1（双向）
    matched = {n for n, v in scores.items() if v > 0}
    for n in matched:
        for ref in referenced_by.get(n, ()):          # 引用命中表的表
            if scores[ref] == 0:
                scores[ref] += _NEIGHBOR_SCORE
        for target in by_name[n].fk_targets:           # 被命中表引用的表
            if target in scores and scores[target] == 0:
                scores[target] += _NEIGHBOR_SCORE

    ranked = [n for n, v in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0])) if v > 0]
    if not ranked:
        # 意图与库存无任何词元可匹配（如纯中文目的）→ 连接度优先的确定性回退
        ranked = sorted(
            names,
            key=lambda n: (-len(referenced_by.get(n, ())), n),
        )
    return ranked[:max_tables]


def _purpose_tokens(purpose: str) -> list[str]:
    """从目的句提取英文词元（小写）；无英文返回空列表。"""
    return [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9_]*", purpose or "")]