import { Popover } from "antd";
import { DownOutlined } from "@ant-design/icons";
import Tooltip from "antd/es/tooltip";
import type { NavItem } from "./Sidebar";

interface Props {
  items: NavItem[];
  selectedKey: string;
  onNavigate: (item: NavItem) => void;
}

/** 折叠态：纯图标导航 + 右侧 Tooltip（移植自 WorkMason SidebarCollapsedIconNav）。
 * 带子项的父项：点击弹出气泡子菜单；叶子项：直接导航。 */
export default function SidebarCollapsedIconNav({ items, selectedKey, onNavigate }: Props) {
  return (
    <div className="dm-icon-nav">
      {items.map((item) => {
        if (item.children && item.children.length) {
          const childActive = item.children.some((c) => c.key === selectedKey);
          return (
            <Popover
              key={item.key}
              trigger="click"
              placement="right"
              content={
                <div className="dm-icon-nav__pop">
                  {item.children.map((c) => (
                    <button
                      key={c.key}
                      type="button"
                      className={`dm-icon-nav__pop-item${
                        selectedKey === c.key ? " dm-icon-nav__pop-item--active" : ""
                      }`}
                      onClick={() => onNavigate(c)}
                    >
                      {c.icon}
                      <span>{c.label}</span>
                    </button>
                  ))}
                </div>
              }
            >
              <button
                type="button"
                className={`dm-icon-nav__btn${childActive ? " dm-icon-nav__btn--active" : ""}`}
              >
                {item.icon}
                <DownOutlined className="dm-icon-nav__pop-caret" />
              </button>
            </Popover>
          );
        }
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
