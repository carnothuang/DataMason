import { useEffect, useMemo, useRef } from "react";
import * as echarts from "echarts/core";
import { BarChart, LineChart, PieChart } from "echarts/charts";
import { GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { EChartsCoreOption } from "echarts/core";
import { QaChart } from "../api";
import { chartPalette } from "../styles/tokens";

// M4-2：ECharts 按需引入（只打包问数据常见图表类型，不引整套图表链路）
echarts.use([BarChart, LineChart, PieChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer]);

function toNumber(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string" && value.trim() !== "") {
    const n = Number(value);
    return Number.isNaN(n) ? null : n;
  }
  return null;
}

/** 规格是否可渲染：x/y 都在结果列内、x≠y，且 y 列至少有一个可数值化的值。
 *  不满足则调用方应退化为表格呈现（非法/不可用规格不出错）。 */
export function isRenderableChart(
  chart: QaChart | null | undefined,
  columns: string[],
  rows: Record<string, unknown>[],
): boolean {
  if (!chart) return false;
  if (!columns.includes(chart.x) || !columns.includes(chart.y) || chart.x === chart.y) return false;
  if (rows.length === 0) return false;
  return rows.some((r) => toNumber(r[chart.y]) !== null);
}

/** 规格驱动生成 ECharts option（确定性映射，不写死具体图表）。 */
export function buildChartOption(
  chart: QaChart,
  rows: Record<string, unknown>[],
): EChartsCoreOption {
  if (chart.type === "pie") {
    return {
      color: chartPalette(),
      tooltip: { trigger: "item" },
      legend: { type: "scroll", bottom: 0 },
      series: [
        {
          type: "pie",
          radius: ["40%", "66%"],
          center: ["50%", "46%"],
          data: rows.map((r) => ({
            name: String(r[chart.x] ?? ""),
            value: toNumber(r[chart.y]) ?? 0,
          })),
        },
      ],
    };
  }
  const categories = rows.map((r) => String(r[chart.x] ?? ""));
  const values = rows.map((r) => toNumber(r[chart.y]) ?? 0);
  return {
    color: chartPalette(),
    tooltip: { trigger: "axis" },
    grid: { left: 8, right: 16, top: 16, bottom: 8, containLabel: true },
    xAxis: { type: "category", data: categories, axisLabel: { interval: 0, rotate: categories.length > 6 ? 30 : 0 } },
    yAxis: { type: "value" },
    series: [
      {
        type: chart.type,
        data: values,
        barMaxWidth: 36,
        smooth: chart.type === "line",
        areaStyle: chart.type === "line" ? {} : undefined,
      },
    ],
  };
}

interface Props {
  chart: QaChart;
  rows: Record<string, unknown>[];
  height?: number;
}

export default function ChartCard({ chart, rows, height = 260 }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const option = useMemo(() => buildChartOption(chart, rows), [chart, rows]);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const inst = echarts.init(el);
    inst.setOption(option);
    const onResize = () => inst.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      inst.dispose();
    };
  }, [option]);

  return <div ref={ref} style={{ width: "100%", height }} />;
}
