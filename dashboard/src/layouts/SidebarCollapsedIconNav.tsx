import { Tooltip } from "antd";
import type { NavItem } from "./Sidebar";

interface Props {
  items: NavItem[];
  selectedKey: string;
  onNavigate: (item: NavItem) => void;
}

/** 折叠态：纯图标导航 + 右侧 Tooltip（移植自 WorkMason SidebarCollapsedIconNav）。 */
export default function SidebarCollapsedIconNav({ items, selectedKey, onNavigate }: Props) {
  return (
    <div className="dm-icon-nav">
      {items.map((item) => {
        const active = selectedKey === item.key;
        return (
          <Tooltip key={item.key} title={item.label} placement="right" mouseEnterDelay={0.2}>
            <button
              type="button"
              className={`dm-icon-nav__btn${active ? " dm-icon-nav__btn--active" : ""}`}
              onClick={() => onNavigate(item)}
            >
              {item.icon}
            </button>
          </Tooltip>
        );
      })}
    </div>
  );
}
