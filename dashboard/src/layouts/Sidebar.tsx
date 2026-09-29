import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { CloseOutlined, DownOutlined } from "@ant-design/icons";
import { useTranslation } from "react-i18next";
import SidebarCollapsedIconNav from "./SidebarCollapsedIconNav";
import "./Sidebar.css";

export const EXPANDED_WIDTH = 220;
export const COLLAPSED_WIDTH = 56;
const NAV_GROUPS_STORAGE_KEY = "datamason:sidebar-nav-groups";

export interface NavItem {
  key: string;
  label: string;
  icon?: ReactNode;
}

export interface NavSection {
  /** 有 groupKey 渲染可折叠分组标题；否则为平铺项。 */
  groupKey?: string;
  items: NavItem[];
}

function loadCollapsedGroups(): Set<string> {
  try {
    const raw = localStorage.getItem(NAV_GROUPS_STORAGE_KEY);
    if (!raw) return new Set();
    const parsed = JSON.parse(raw) as unknown;
    if (Array.isArray(parsed)) {
      return new Set(parsed.filter((x): x is string => typeof x === "string"));
    }
  } catch {
    /* localStorage 不可用 */
  }
  return new Set();
}

function saveCollapsedGroups(collapsed: Set<string>): void {
  try {
    localStorage.setItem(NAV_GROUPS_STORAGE_KEY, JSON.stringify([...collapsed]));
  } catch {
    /* 忽略持久化失败 */
  }
}

interface SidebarProps {
  sections: NavSection[];
  selectedKey: string;
  collapsed: boolean;
  onToggle: () => void;
  onNavigate: (item: NavItem) => void;
  brand: ReactNode;
  footer?: ReactNode;
  isMobile?: boolean;
}

function NavItems({
  items,
  selectedKey,
  onNavigate,
}: {
  items: NavItem[];
  selectedKey: string;
  onNavigate: (item: NavItem) => void;
}) {
  return (
    <>
      {items.map((item) => {
        const active = selectedKey === item.key;
        return (
          <button
            key={item.key}
            type="button"
            className={`dm-nav-item${active ? " dm-nav-item--active" : ""}`}
            onClick={() => onNavigate(item)}
          >
            {item.icon && <span className="dm-nav-item__icon">{item.icon}</span>}
            <span className="dm-nav-item__label">{item.label}</span>
          </button>
        );
      })}
    </>
  );
}

/**
 * 通用侧栏（移植自 WorkMason layouts/Sidebar）：
 * 分组可折叠、折叠态纯图标 + Tooltip、移动端覆盖式抽屉。
 * 桌面端右侧拖拽边由调用方（MainLayout / Workspace）用 RailEdgeControl 承载。
 */
export default function Sidebar({
  sections,
  selectedKey,
  collapsed,
  onToggle,
  onNavigate,
  brand,
  footer,
  isMobile,
}: SidebarProps) {
  const { t } = useTranslation();
  const isRailCollapsed = collapsed && !isMobile;
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(() => loadCollapsedGroups());

  const activeGroupKey = useMemo(
    () =>
      sections.find((s) => s.groupKey && s.items.some((i) => i.key === selectedKey))?.groupKey,
    [sections, selectedKey],
  );

  useEffect(() => {
    if (!activeGroupKey) return;
    setCollapsedGroups((prev) => {
      if (!prev.has(activeGroupKey)) return prev;
      const next = new Set(prev);
      next.delete(activeGroupKey);
      saveCollapsedGroups(next);
      return next;
    });
  }, [activeGroupKey, selectedKey]);

  const toggleGroup = useCallback((key: string) => {
    setCollapsedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      saveCollapsedGroups(next);
      return next;
    });
  }, []);

  const visibleItems = useMemo(
    () => sections.flatMap((s) => (s.groupKey && collapsedGroups.has(s.groupKey) ? [] : s.items)),
    [sections, collapsedGroups],
  );

  const navBody = isRailCollapsed ? (
    <div style={{ padding: "8px 0" }}>
      <SidebarCollapsedIconNav items={visibleItems} selectedKey={selectedKey} onNavigate={onNavigate} />
    </div>
  ) : (
    <div style={{ padding: "8px 12px" }}>
      {sections.map((section, index) => {
        const sectionKey = section.groupKey ?? `flat-${index}`;
        if (!section.groupKey) {
          return (
            <div key={sectionKey} className="dm-nav-group">
              <NavItems items={section.items} selectedKey={selectedKey} onNavigate={onNavigate} />
            </div>
          );
        }
        const groupCollapsed = collapsedGroups.has(section.groupKey);
        return (
          <div key={sectionKey} className="dm-nav-group">
            <button
              type="button"
              className="dm-nav-group__header"
              onClick={() => toggleGroup(section.groupKey!)}
              aria-expanded={!groupCollapsed}
            >
              <span className="dm-nav-group__label">{section.groupKey}</span>
              <DownOutlined
                className={`dm-nav-group__chevron${
                  groupCollapsed ? " dm-nav-group__chevron--folded" : ""
                }`}
              />
            </button>
            {!groupCollapsed && (
              <div className="dm-nav-group__items">
                <NavItems items={section.items} selectedKey={selectedKey} onNavigate={onNavigate} />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );

  if (isMobile) {
    return (
      <div
        style={{
          position: "fixed",
          top: 0,
          left: 0,
          height: "100dvh",
          width: EXPANDED_WIDTH,
          background: "var(--fn-sidebar-bg)",
          borderRight: "1px solid var(--fn-sidebar-border)",
          zIndex: 100,
          display: "flex",
          flexDirection: "column",
          transform: collapsed ? "translateX(-100%)" : "translateX(0)",
          transition: "transform 0.25s cubic-bezier(0.4, 0, 0.2, 1)",
          boxShadow: collapsed ? "none" : "var(--fn-shadow-lg)",
        }}
      >
        <div
          className="dm-sidebar__brand"
          style={{ height: 56, justifyContent: "space-between", padding: "0 12px 0 16px" }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0, flex: 1 }}>
            {brand}
          </div>
          <button type="button" className="dm-sidebar__icon-btn" onClick={onToggle} aria-label={t("sidebar.collapse")}>
            <CloseOutlined />
          </button>
        </div>
        <div className="dm-sidebar__scroll">{navBody}</div>
        {footer && (
          <div
            className="dm-sidebar__footer"
            style={{ paddingBottom: "calc(8px + env(safe-area-inset-bottom, 0px))" }}
          >
            {footer}
          </div>
        )}
      </div>
    );
  }

  return (
    <div
      className="dm-sidebar"
      style={{
        width: isRailCollapsed ? COLLAPSED_WIDTH : EXPANDED_WIDTH,
        minWidth: isRailCollapsed ? COLLAPSED_WIDTH : EXPANDED_WIDTH,
      }}
    >
      <div
        className="dm-sidebar__brand"
        style={{ padding: isRailCollapsed ? "12px 0" : "14px 14px 10px" }}
      >
        {brand}
      </div>
      <div className="dm-sidebar__scroll">{navBody}</div>
      {footer && <div className="dm-sidebar__footer">{footer}</div>}
    </div>
  );
}
