import { Typography } from "antd";
import type { FormOptionItem } from "../api";
import { maskText } from "../pages/workspace/demo";

const { Text } = Typography;

/** 级联选项 → 每个 value 的完整路径标签（顶层=自身 label；子级=「父 / 子」）。 */
function buildCascaderPathMap(
  options: FormOptionItem[],
): Map<string, string> {
  const byValue = new Map(options.map((o) => [o.value, o]));
  const cache = new Map<string, string>();
  const pathOf = (value: string): string => {
    const hit = cache.get(value);
    if (hit !== undefined) return hit;
    const node = byValue.get(value);
    if (!node) {
      cache.set(value, value);
      return value;
    }
    if (!node.parent_value) {
      cache.set(value, node.label);
      return node.label;
    }
    const parent = pathOf(node.parent_value);
    const full = parent ? `${parent} / ${node.label}` : node.label;
    cache.set(value, full);
    return full;
  };
  const map = new Map<string, string>();
  for (const o of options) map.set(o.value, pathOf(o.value));
  return map;
}

interface ValueLabelProps {
  value: unknown;
  /** 表单控件类型（radio/select/cascader/checkbox 消费 options；其余忽略）。 */
  controlType?: string | null;
  options?: FormOptionItem[];
  /** 该列的外键标签映射（{ id串: 标签 }，来自 rows.ref_labels[列名]）。 */
  refLabels?: Record<string, string>;
  /** 演示脱敏（沿用列表既有行为：脱敏只改展示层）。 */
  masked?: boolean;
}

/**
 * 显示值 ↔ 落库值 的统一读侧转换点（0.15.0 M9-5 / DR-49）。
 *
 * - 选项类（radio/select/cascader/checkbox 且有候选项）：value → label；级联拼完整路径；
 * - 外键类：refLabels[String(value)] → 标签；
 * - 均查不到（脏值/选项被删/导入值）：兜底原值；null/undefined 显示「—」。
 * 只做读侧渲染，绝不改写库存值。
 */
export default function ValueLabel({
  value,
  controlType,
  options,
  refLabels,
  masked,
}: ValueLabelProps) {
  if (value === null || value === undefined || value === "") {
    return <Text type="secondary">—</Text>;
  }
  if (masked) {
    return <>{String(maskText(value))}</>;
  }

  const hasOptions = !!(options && options.length);
  if (
    hasOptions &&
    (controlType === "radio" ||
      controlType === "select" ||
      controlType === "cascader" ||
      controlType === "checkbox")
  ) {
    const raw = String(value);
    if (controlType === "cascader") {
      const full = buildCascaderPathMap(options!).get(raw);
      if (full) return <>{full}</>;
    } else {
      const hit = options!.find((o) => String(o.value) === raw);
      if (hit) return <>{hit.label}</>;
    }
    // 候选无匹配 → 落到下方兜底原值
  }

  if (refLabels) {
    const label = refLabels[String(value)];
    if (label !== undefined) return <>{label}</>;
  }

  return <>{String(value)}</>;
}
