import { createContext, useContext } from "react";

/** M3-3 演示模式（展厅）状态：demo=进入演示；masked=「演示专用数据」脱敏展示。
 *  脱敏只作用于展示层（表格文本），不落库、不动真实数据。 */
export interface DemoState {
  demo: boolean;
  masked: boolean;
}

export const DemoContext = createContext<DemoState>({ demo: false, masked: false });

export const useDemo = (): DemoState => useContext(DemoContext);

const MASK = "\u25CF";

/** 展示层脱敏：非空字符串保留首尾各 1 位、中间以 ● 代替；其余类型原样返回。
 *  纯函数、确定性——开/关得到两套视图，底层数据零变化。 */
export function maskText(value: unknown): unknown {
  if (typeof value !== "string" || value.length === 0) return value;
  if (value.length === 1) return MASK;
  if (value.length === 2) return value[0] + MASK;
  return value[0] + MASK.repeat(value.length - 2) + value[value.length - 1];
}
