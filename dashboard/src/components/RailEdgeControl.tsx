/* 侧栏垂直拖拽边控件（移植自 WorkMason components/RailEdgeControl）。 */
import "./RailEdgeControl.css";

interface RailEdgeControlProps {
  /** 该面板当前是否展开。 */
  expanded: boolean;
  onToggle: () => void;
  /** end = 展开面板右侧（收起按钮在左）；start = 折叠面板原位（展开按钮在右）。 */
  side?: "end" | "start";
  label?: string;
}

/** 宽角箭头（无圆形底盘）。 */
function WideChevron({ dir }: { dir: "left" | "right" }) {
  return (
    <svg className="dm-rail__chevron" viewBox="0 0 10 22" width="10" height="22" aria-hidden>
      <path
        d={dir === "left" ? "M7.5 2.5 L2.5 11 L7.5 19.5" : "M2.5 2.5 L7.5 11 L2.5 19.5"}
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export default function RailEdgeControl({
  expanded,
  onToggle,
  side = "end",
  label,
}: RailEdgeControlProps) {
  const ariaLabel = label ?? (expanded ? "收起侧栏" : "展开侧栏");
  return (
    <div
      className={`dm-rail dm-rail--${side} ${
        expanded ? "dm-rail--expanded" : "dm-rail--collapsed"
      }`}
    >
      <div className="dm-rail__line" aria-hidden />
      <button type="button" className="dm-rail__btn" onClick={onToggle} aria-label={ariaLabel} title={ariaLabel}>
        <WideChevron dir={expanded ? "left" : "right"} />
      </button>
    </div>
  );
}
