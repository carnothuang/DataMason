import { MenuOutlined } from "@ant-design/icons";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useTheme } from "../context/ThemeContext";
import "./Sidebar.css";

interface HeaderProps {
  onToggle: () => void;
  /** 右侧附加操作区（工作台放「问数据 / 演示」等）。 */
  extra?: ReactNode;
}

/** 移动端顶部栏：导航开关 + 品牌（移植自 WorkMason layouts/Header）。 */
export default function Header({ onToggle, extra }: HeaderProps) {
  const { isDark } = useTheme();
  const { t } = useTranslation();
  return (
    <div className="dm-header">
      <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0 }}>
        <button type="button" className="dm-sidebar__icon-btn" onClick={onToggle} aria-label={t("header.openNav")}>
          <MenuOutlined />
        </button>
        <img
          src={isDark ? "/logo_name_dark.png" : "/logo_name.png"}
          alt="DataMason"
          style={{ height: 26, width: "auto", maxWidth: 160, objectFit: "contain", display: "block" }}
        />
      </div>
      {extra && <div style={{ display: "flex", alignItems: "center", gap: 8 }}>{extra}</div>}
    </div>
  );
}
