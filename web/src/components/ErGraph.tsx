/** 数据关系 ER 图（M2-8，简版）：环形布局，外键指向画连线，节点标业务名。
 *  同一组件两种粗细：
 *  - 提案卡缩略图（compact）：小号，不带连线标签
 *  - 数据蓝图全域图（默认）：大号，连线标"来源列 → 目标表"
 *  输入就是提案卡 / 蓝图已有的 tables（含 is_fk/ref_table），不额外请求。 */
interface ErNode {
  name: string;
  biz_name: string;
  columns?: { name: string; is_fk?: boolean; ref_table?: string | null }[];
}

interface Props {
  tables: ErNode[];
  width?: number;
  height?: number;
  compact?: boolean;
}

interface Placed {
  node: ErNode;
  x: number;
  y: number;
}

const NODE_W = { big: 150, small: 96 };
const NODE_H = { big: 64, small: 38 };
const PAD = 24;

export default function ErGraph({ tables, width = 320, height = 200, compact }: Props) {
  const n = tables.length;
  const nodeW = compact ? NODE_W.small : NODE_W.big;
  const nodeH = compact ? NODE_H.small : NODE_H.big;

  // 边：FK 列 → ref_table（target 必须在图内），去重 + 去自环
  const edges: { from: string; to: string; label: string }[] = [];
  const seen = new Set<string>();
  for (const t of tables) {
    for (const c of t.columns || []) {
      if (!c.is_fk || !c.ref_table || c.ref_table === t.name) continue;
      if (!tables.some((x) => x.name === c.ref_table)) continue;
      const key = `${t.name}->${c.ref_table}`;
      if (seen.has(key)) continue;
      seen.add(key);
      edges.push({ from: t.name, to: c.ref_table, label: compact ? "" : c.name });
    }
  }

  // 环形布局（确定性：先按表名排序）
  const sorted = [...tables].sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
  const cx = width / 2;
  const cy = height / 2;
  const radius =
    n <= 1 ? 0 : Math.min(cx, cy) - Math.max(nodeW, nodeH) / 2 - PAD;
  const placed: Placed[] = sorted.map((node, i) => {
    if (n <= 1) return { node, x: cx - nodeW / 2, y: cy - nodeH / 2 };
    const angle = (2 * Math.PI * i) / n - Math.PI / 2;
    return {
      node,
      x: cx + radius * Math.cos(angle) - nodeW / 2,
      y: cy + radius * Math.sin(angle) - nodeH / 2,
    };
  });
  const centerOf = (name: string) => {
    const p = placed.find((x) => x.node.name === name);
    return p ? { x: p.x + nodeW / 2, y: p.y + nodeH / 2 } : { x: 0, y: 0 };
  };

  if (n === 0) {
    return (
      <div
        style={{
          width, height, display: "flex", alignItems: "center", justifyContent: "center",
          color: "#999", fontSize: 12,
        }}
      >
        暂无表关系
      </div>
    );
  }

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} style={{ display: "block" }}>
      {/* 连线层（先画，节点盖在上面） */}
      {edges.map((e) => {
        const a = centerOf(e.from);
        const b = centerOf(e.to);
        return (
          <g key={`${e.from}-${e.to}`}>
            <line
              x1={a.x} y1={a.y} x2={b.x} y2={b.y}
              stroke="#C15F3C" strokeOpacity={0.55} strokeWidth={compact ? 1.2 : 1.6}
            />
            <circle cx={b.x} cy={b.y} r={compact ? 2.5 : 3.5} fill="#C15F3C" fillOpacity={0.8} />
            {!compact && e.label && (
              <text
                x={(a.x + b.x) / 2} y={(a.y + b.y) / 2 - 4}
                fill="#888" fontSize={11} textAnchor="middle"
              >
                {e.label}
              </text>
            )}
          </g>
        );
      })}
      {/* 节点层 */}
      {placed.map(({ node, x, y }) => (
        <g key={node.name}>
          <rect
            x={x} y={y} width={nodeW} height={nodeH} rx={compact ? 6 : 8}
            fill="#fff" stroke={compact ? "#d9d9d9" : "#C15F3C"}
            strokeWidth={compact ? 1 : 1.4}
          />
          <text
            x={x + nodeW / 2} y={y + nodeH / 2 - (compact ? 2 : 6)}
            fill="#333" fontSize={compact ? 11 : 13}
            fontWeight={compact ? 600 : 700} textAnchor="middle"
          >
            {node.biz_name || node.name}
          </text>
          {!compact && (
            <text x={x + nodeW / 2} y={y + nodeH / 2 + 12} fill="#999" fontSize={10} textAnchor="middle">
              {node.name}
            </text>
          )}
        </g>
      ))}
    </svg>
  );
}