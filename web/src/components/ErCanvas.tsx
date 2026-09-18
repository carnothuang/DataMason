/** M6-3 交互式 ER 画布（自研，零新增依赖，DR-36）。
 *
 * 在只读 ErGraph 的环形布局基础上扩展为可交互：节点增删/改名/拖动、
 * 关系连线增删（弹层定外键列名）、字段展开渲染；字段行编辑复用既有交互。
 *
 * 单一数据源 = 字段定义（DR-38）：画布只是视图 + 编辑入口，所有变更都
 * 通过 dispatch 写回 HybridDraft；连线由外键列派生，图与数据永不两套真相。
 */
import { useEffect, useMemo, useState } from "react";
import {
  App,
  Button,
  Empty,
  Input,
  InputNumber,
  Modal,
  Segmented,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  HybridAction,
  HybridColumn,
  HybridDraft,
  HybridTable,
  edgesOf,
  isOwnershipName,
  normalizeColumn,
} from "../pages/deploy/hybridDraft";

const { Text } = Typography;

const TYPE_OPTIONS = [
  { value: "INTEGER", label: "整数 INTEGER" },
  { value: "BIGINT", label: "长整数 BIGINT" },
  { value: "VARCHAR", label: "短文本 VARCHAR" },
  { value: "TEXT", label: "长文本 TEXT" },
  { value: "BOOLEAN", label: "布尔 BOOLEAN" },
  { value: "DATETIME", label: "日期时间 DATETIME" },
  { value: "DECIMAL", label: "金额 DECIMAL" },
  { value: "FILE", label: "附件 FILE" },
];

const NAME_RE = /^[a-zA-Z_][a-zA-Z0-9_]*$/;
const NODE_W = 210;
const HEADER_H = 52;
const ROW_H = 20;
const MAX_ROWS = 6;

interface Props {
  draft: HybridDraft;
  dispatch: (action: HybridAction) => void;
  selected: string | null;
  onSelect: (name: string | null) => void;
  width?: number;
  height?: number;
  layout?: "ring" | "grid";
  onLayoutChange?: (layout: "ring" | "grid") => void;
}

interface Pos {
  x: number;
  y: number;
}

/** 确定性布局：环形（默认，沿用 ErGraph）或网格。 */
function computeLayout(tables: HybridTable[], width: number, height: number, layout: "ring" | "grid"): Record<string, Pos> {
  const out: Record<string, Pos> = {};
  const n = tables.length;
  if (n === 0) return out;
  if (layout === "grid") {
    const cols = Math.max(1, Math.ceil(Math.sqrt(n)));
    const stepX = width / (cols + 0.5);
    const stepY = 150;
    tables.forEach((t, i) => {
      const cx = ((i % cols) + 1) * stepX;
      const cy = (Math.floor(i / cols) + 1) * stepY;
      out[t.name] = { x: cx - NODE_W / 2, y: cy - HEADER_H / 2 };
    });
    return out;
  }
  const cx = width / 2;
  const cy = height / 2;
  const radius = n <= 1 ? 0 : Math.max(Math.min(cx, cy) - 140, 90);
  tables.forEach((t, i) => {
    if (n <= 1) {
      out[t.name] = { x: cx - NODE_W / 2, y: cy - HEADER_H / 2 };
      return;
    }
    const angle = (2 * Math.PI * i) / n - Math.PI / 2;
    out[t.name] = {
      x: cx + radius * Math.cos(angle) - NODE_W / 2,
      y: cy + radius * Math.sin(angle) - HEADER_H / 2,
    };
  });
  return out;
}

export default function ErCanvas({
  draft, dispatch, selected, onSelect, width = 860, height = 460, layout = "ring", onLayoutChange,
}: Props) {
  const { modal, message } = App.useApp();
  const [positions, setPositions] = useState<Record<string, Pos>>({});
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [linking, setLinking] = useState<string | null>(null);
  const [tableModal, setTableModal] = useState<{ mode: "add" | "rename"; name?: string } | null>(null);
  const [draftName, setDraftName] = useState("");
  const [draftBiz, setDraftBiz] = useState("");

  const baseLayout = useMemo(
    () => computeLayout(draft.tables, width, height, layout),
    [draft.tables, width, height, layout],
  );
  const posOf = (name: string): Pos => positions[name] ?? baseLayout[name] ?? { x: 0, y: 0 };

  const edges = useMemo(() => edgesOf(draft), [draft]);
  const tableMap = useMemo(
    () => Object.fromEntries(draft.tables.map((t) => [t.name, t])),
    [draft],
  );

  // 节点拖动（草稿态内存微调，不持久化）
  const [drag, setDrag] = useState<{ name: string; sx: number; sy: number; ox: number; oy: number } | null>(null);
  useEffect(() => {
    if (!drag) return;
    const move = (e: MouseEvent) => {
      setPositions((prev) => ({
        ...prev,
        [drag.name]: { x: drag.ox + (e.clientX - drag.sx), y: drag.oy + (e.clientY - drag.sy) },
      }));
    };
    const up = () => setDrag(null);
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
    return () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
  }, [drag]);

  const startDrag = (e: React.MouseEvent, name: string) => {
    const p = posOf(name);
    setDrag({ name, sx: e.clientX, sy: e.clientY, ox: p.x, oy: p.y });
  };

  const openAdd = () => {
    setDraftName("");
    setDraftBiz("");
    setTableModal({ mode: "add" });
  };
  const openRename = (t: HybridTable) => {
    setDraftName(t.name);
    setDraftBiz(t.biz_name);
    setTableModal({ mode: "rename", name: t.name });
  };
  const submitTable = () => {
    const name = draftName.trim();
    if (!NAME_RE.test(name)) {
      message.error("表名需以字母/下划线开头，只含字母数字下划线");
      return;
    }
    if (tableModal?.mode === "add") {
      if (tableMap[name]) {
        message.error("表名已存在");
        return;
      }
      dispatch({ type: "addTable", name, biz_name: draftBiz });
    } else if (tableModal?.mode === "rename" && tableModal.name) {
      dispatch({ type: "updateTable", name: tableModal.name, patch: { name, biz_name: draftBiz } });
    }
    setTableModal(null);
  };

  const removeTable = (t: HybridTable) => {
    modal.confirm({
      title: `删除表「${t.biz_name || t.name}」？`,
      content: "该表及其字段会从草稿移除；指向它的关系线一并消失。此操作只影响草稿。",
      okText: "删除",
      okButtonProps: { danger: true },
      cancelText: "取消",
      onOk: () => dispatch({ type: "removeTable", name: t.name }),
    });
  };

  const removeEdge = (from: string, column: string, to: string) => {
    modal.confirm({
      title: "删除这条关系？",
      content: `${from}.${column} → ${to}.id（同时移除该外键列）`,
      okText: "删除",
      okButtonProps: { danger: true },
      cancelText: "取消",
      onOk: () => dispatch({ type: "removeRelation", from, column }),
    });
  };

  const onNodeClick = (name: string) => {
    if (linking && linking !== name) {
      const target = tableMap[name];
      // 新建连线 → 弹层定外键列名（默认 <目标表>_id）
      let colName = `${name}_id`;
      modal.confirm({
        title: `从「${tableMap[linking].biz_name || linking}」连到「${target.biz_name || name}」`,
        content: (
          <div style={{ marginTop: 8 }}>
            <Text type="secondary" style={{ fontSize: 12 }}>外键列名（默认 {name}_id）</Text>
            <Input defaultValue={colName} onChange={(e) => { colName = e.target.value; }} />
          </div>
        ),
        okText: "建立关系",
        cancelText: "取消",
        onOk: () => dispatch({ type: "addRelation", from: linking, to: name, columnName: colName }),
      });
      setLinking(null);
      return;
    }
    onSelect(name);
  };

  const fieldRows = (t: HybridTable) => t.columns.length;

  const nodeHeight = (t: HybridTable) => {
    if (!expanded[t.name]) return HEADER_H + 6;
    return HEADER_H + Math.min(fieldRows(t), MAX_ROWS) * ROW_H + (fieldRows(t) > MAX_ROWS ? ROW_H : 0) + 10;
  };

  const nodeState = (t: HybridTable) => {
    const guided = t.columns.some((c) => !c.is_pk && !c.is_ownership);
    return { guided, selected: selected === t.name, linking: linking === t.name };
  };

  return (
    <div>
      <Space style={{ marginBottom: 8 }} wrap>
        <Button size="small" onClick={openAdd}>+ 加表</Button>
        <Segmented
          size="small"
          value={layout}
          onChange={(v) => onLayoutChange?.(v as "ring" | "grid")}
          options={[{ label: "环形", value: "ring" }, { label: "网格", value: "grid" }]}
        />
        {linking ? (
          <Tag color="processing" closable onClose={() => setLinking(null)}>
            选择要连到的目标表…
          </Tag>
        ) : (
          <Text type="secondary" style={{ fontSize: 12 }}>
            拖动节点微调位置；节点上「连线」开始画关系；点线可删除
          </Text>
        )}
      </Space>

      <div style={{ display: "flex", gap: 12, alignItems: "flex-start" }}>
        <svg
          width={width}
          height={height}
          viewBox={`0 0 ${width} ${height}`}
          style={{ display: "block", background: "#fcfaf7", border: "1px solid #f0e6dd", borderRadius: 10, flex: "0 0 auto" }}
          onClick={() => { if (linking) setLinking(null); }}
        >
          <defs>
            <marker id="er-arrow" markerWidth="9" markerHeight="9" refX="8" refY="3" orient="auto">
              <path d="M0,0 L8,3 L0,6 Z" fill="#C15F3C" fillOpacity={0.85} />
            </marker>
          </defs>
          {draft.tables.length === 0 && (
            <text x={width / 2} y={height / 2} textAnchor="middle" fill="#bbb" fontSize={13}>
              还没有表，点左上角「+ 加表」开始
            </text>
          )}
          {/* 连线层 */}
          {edges.map((e) => {
            const a = posOf(e.from);
            const b = posOf(e.to);
            const x1 = a.x + NODE_W / 2, y1 = a.y + HEADER_H / 2;
            const x2 = b.x + NODE_W / 2, y2 = b.y + HEADER_H / 2;
            const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
            return (
              <g key={`${e.from}-${e.column}-${e.to}`}>
                <line
                  x1={x1} y1={y1} x2={x2} y2={y2}
                  stroke="#C15F3C" strokeOpacity={0.55} strokeWidth={1.6}
                  markerEnd="url(#er-arrow)"
                  style={{ cursor: "pointer" }}
                  onClick={(ev) => { ev.stopPropagation(); removeEdge(e.from, e.column, e.to); }}
                />
                <text x={mx} y={my - 4} fill="#999" fontSize={10} textAnchor="middle" style={{ cursor: "pointer" }}
                      onClick={(ev) => { ev.stopPropagation(); removeEdge(e.from, e.column, e.to); }}>
                  {e.column} · 点击删除
                </text>
              </g>
            );
          })}
          {/* 节点层 */}
          {draft.tables.map((t) => {
            const p = posOf(t.name);
            const h = nodeHeight(t);
            const st = nodeState(t);
            const drawn = t.columns.slice(0, MAX_ROWS);
            return (
              <g key={t.name}>
                <rect
                  x={p.x} y={p.y} width={NODE_W} height={h} rx={9}
                  fill="#fff"
                  stroke={st.selected ? "#C15F3C" : st.linking ? "#1677ff" : "#e2d5c9"}
                  strokeWidth={st.selected || st.linking ? 2 : 1.3}
                  style={{ cursor: "grab" }}
                  onMouseDown={(e) => { if (!linking) startDrag(e, t.name); }}
                  onClick={(e) => { e.stopPropagation(); onNodeClick(t.name); }}
                />
                {/* 表头 */}
                <text x={p.x + 12} y={p.y + 22} fontSize={13} fontWeight={700} fill="#333"
                      style={{ cursor: "pointer" }} onClick={(e) => { e.stopPropagation(); onNodeClick(t.name); }}>
                  {t.biz_name || t.name}
                </text>
                <text x={p.x + 12} y={p.y + 40} fontSize={10} fill="#999">{t.name}</text>
                {!st.guided && (
                  <text x={p.x + NODE_W - 12} y={p.y + 22} fontSize={10} fill="#d46b08" textAnchor="end">待引导字段</text>
                )}
                {t.columns.length > 0 && (
                  <text x={p.x + NODE_W - 12} y={p.y + 40} fontSize={10} fill="#1677ff" textAnchor="end"
                        style={{ cursor: "pointer" }}
                        onClick={(e) => { e.stopPropagation(); setExpanded((m) => ({ ...m, [t.name]: !m[t.name] })); }}>
                    {expanded[t.name] ? "折叠" : `展开 ${t.columns.length} 列`}
                  </text>
                )}
                {/* 字段列表（展开） */}
                {expanded[t.name] && drawn.map((c, i) => (
                  <g key={c.key}>
                    <text x={p.x + 14} y={p.y + HEADER_H + 14 + i * ROW_H} fontSize={11} fill={c.is_fk ? "#1677ff" : "#555"}>
                      {(c.biz_name || c.name)}
                      {c.is_pk ? " ·主键" : ""}
                      {c.is_fk ? ` ·→${c.ref_table}` : ""}
                      {c.unique ? " ·唯一" : ""}
                    </text>
                    <text x={p.x + NODE_W - 12} y={p.y + HEADER_H + 14 + i * ROW_H} fontSize={10} fill="#aaa" textAnchor="end">
                      {c.type}
                    </text>
                  </g>
                ))}
                {expanded[t.name] && fieldRows(t) > MAX_ROWS && (
                  <text x={p.x + 14} y={p.y + HEADER_H + 14 + MAX_ROWS * ROW_H} fontSize={10} fill="#999">
                    …共 {fieldRows(t)} 列（在右侧面板编辑）
                  </text>
                )}
                {/* 节点操作 */}
                <g>
                  <text x={p.x + 12} y={p.y + h - 8} fontSize={10} fill="#C15F3C"
                        style={{ cursor: "pointer" }}
                        onClick={(e) => { e.stopPropagation(); setLinking(t.name); onSelect(t.name); }}>
                    连线
                  </text>
                  <text x={p.x + 52} y={p.y + h - 8} fontSize={10} fill="#666"
                        style={{ cursor: "pointer" }}
                        onClick={(e) => { e.stopPropagation(); openRename(t); }}>
                    改名
                  </text>
                  <text x={p.x + 92} y={p.y + h - 8} fontSize={10} fill="#cf1322"
                        style={{ cursor: "pointer" }}
                        onClick={(e) => { e.stopPropagation(); removeTable(t); }}>
                    删表
                  </text>
                </g>
              </g>
            );
          })}
        </svg>

        <div style={{ flex: 1, minWidth: 320 }}>
          {selected && tableMap[selected] ? (
            <FieldInspector
              table={tableMap[selected]}
              tables={draft.tables}
              dispatch={dispatch}
            />
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="选中一张表以编辑字段" />
          )}
        </div>
      </div>

      <Modal
        title={tableModal?.mode === "add" ? "新增表" : "修改表"}
        open={!!tableModal}
        onCancel={() => setTableModal(null)}
        onOk={submitTable}
        okText="确定"
        cancelText="取消"
        destroyOnClose
      >
        <Space direction="vertical" style={{ width: "100%" }}>
          <div>
            <Text type="secondary" style={{ fontSize: 12 }}>表名（英文 snake_case）</Text>
            <Input value={draftName} onChange={(e) => setDraftName(e.target.value)} placeholder="如 customers" />
          </div>
          <div>
            <Text type="secondary" style={{ fontSize: 12 }}>业务名</Text>
            <Input value={draftBiz} onChange={(e) => setDraftBiz(e.target.value)} placeholder="如 客户" />
          </div>
        </Space>
      </Modal>
    </div>
  );
}

/** 字段行编辑面板（复用既有 FieldEditor 交互形态，写回同一草稿）。 */
function FieldInspector({
  table, tables, dispatch,
}: {
  table: HybridTable;
  tables: HybridTable[];
  dispatch: (action: HybridAction) => void;
}) {
  const update = (key: string, patch: Partial<HybridColumn>) =>
    dispatch({ type: "updateColumn", table: table.name, key, patch });

  const columns: ColumnsType<HybridColumn> = [
    {
      title: "字段名（英文）",
      dataIndex: "name",
      width: 170,
      render: (_v, c) => {
        const locked = c.is_pk || c.is_ownership || isOwnershipName(c.name);
        return locked ? (
          <Space size={4}>
            <Text code>{c.name}</Text>
            {c.is_pk && <Tag color="gold">主键</Tag>}
            {c.is_ownership && <Tag>系统</Tag>}
          </Space>
        ) : (
          <Input
            size="small"
            value={c.name}
            status={NAME_RE.test(c.name) ? undefined : "error"}
            onChange={(e) => update(c.key, { name: e.target.value.trim() })}
          />
        );
      },
    },
    {
      title: "业务名",
      dataIndex: "biz_name",
      width: 120,
      render: (_v, c) => (
        <Input size="small" value={c.biz_name} onChange={(e) => update(c.key, { biz_name: e.target.value })} />
      ),
    },
    {
      title: "类型",
      dataIndex: "type",
      width: 150,
      render: (_v, c) =>
        c.is_pk || c.is_fk ? (
          <Text type="secondary">{c.type}</Text>
        ) : (
          <Select
            size="small"
            style={{ width: "100%" }}
            value={c.type}
            options={TYPE_OPTIONS}
            onChange={(v) => update(c.key, { type: v, length: v === "VARCHAR" ? c.length ?? 255 : null })}
          />
        ),
    },
    {
      title: "长度",
      dataIndex: "length",
      width: 90,
      render: (_v, c) =>
        c.type === "VARCHAR" && !c.is_pk && !c.is_fk ? (
          <InputNumber
            size="small"
            min={1}
            max={4000}
            value={c.length ?? undefined}
            onChange={(v) => update(c.key, { length: v ?? null })}
          />
        ) : (
          <Text type="secondary">—</Text>
        ),
    },
    {
      title: "可空",
      dataIndex: "nullable",
      width: 60,
      render: (_v, c) =>
        c.is_pk ? (
          <Text type="secondary">否</Text>
        ) : (
          <input type="checkbox" checked={c.nullable} onChange={(e) => update(c.key, { nullable: e.target.checked })} />
        ),
    },
    {
      title: "唯一",
      dataIndex: "unique",
      width: 60,
      render: (_v, c) =>
        c.is_pk ? (
          <Text type="secondary">—</Text>
        ) : (
          <input type="checkbox" checked={c.unique} onChange={(e) => update(c.key, { unique: e.target.checked })} />
        ),
    },
    {
      title: "外键目标",
      dataIndex: "ref_table",
      width: 150,
      render: (_v, c) =>
        c.is_fk ? (
          <Select
            size="small"
            style={{ width: "100%" }}
            value={c.ref_table ?? undefined}
            options={tables
              .filter((t) => t.name !== table.name)
              .map((t) => ({ value: t.name, label: `${t.biz_name || t.name}（${t.name}）` }))}
            onChange={(v) => update(c.key, { ref_table: v, ref_column: "id" })}
          />
        ) : (
          <Text type="secondary">—</Text>
        ),
    },
    {
      title: "操作",
      width: 70,
      render: (_v, c) =>
        c.is_pk || c.is_ownership ? (
          <Text type="secondary" style={{ fontSize: 12 }}>受保护</Text>
        ) : (
          <Button
            size="small"
            type="link"
            danger
            onClick={() => dispatch({ type: "removeColumn", table: table.name, key: c.key })}
          >
            删除
          </Button>
        ),
    },
  ];

  return (
    <div>
      <Space style={{ marginBottom: 8 }}>
        <b>{table.biz_name || table.name}</b>
        <Tag>{table.name}</Tag>
        <Button
          size="small"
          type="dashed"
          onClick={() => dispatch({
            type: "addColumn",
            table: table.name,
            column: normalizeColumn({ name: "new_field", biz_name: "新字段", type: "VARCHAR" }),
          })}
        >
          + 加字段
        </Button>
      </Space>
      <Table<HybridColumn>
        rowKey="key"
        size="small"
        dataSource={table.columns}
        columns={columns}
        pagination={false}
        scroll={{ y: 340 }}
      />
    </div>
  );
}
