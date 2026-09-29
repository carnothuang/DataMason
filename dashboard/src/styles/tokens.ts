/**
 * --fn-* 设计 token 的运行时读取。
 *
 * CSS 变量可直接用于 DOM / SVG；但 ECharts / canvas 与 antd 主题 token 只接受字面色值，
 * 统一从这里读取，保证与 `dashboard/src/styles/theme.css` 单一数据源一致。
 * 明暗主题切换后重新调用即可拿到新值。
 */
export function cssVar(name: string, fallback: string): string {
  if (typeof document === "undefined") return fallback;
  const value = getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim();
  return value || fallback;
}

/** 数据可视化分类色板：首色取品牌 token，其余为既有服务语义色（保持克制）。 */
export function chartPalette(): string[] {
  return [
    cssVar("--fn-color-brand", "#4c545e"),
    "#5B8FF9",
    "#61A0A8",
    "#9270CA",
    "#F6BD16",
  ];
}
