import { useCallback, useEffect, useState } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { App as AntApp } from "antd";
import Sidebar, { type NavItem } from "./Sidebar";
import RailEdgeControl from "../components/RailEdgeControl";
import Header from "./Header";
import { buildConsoleNav } from "./sidebarNav";
import { useIsMobile } from "../hooks/useIsMobile";
import { useTheme } from "../context/ThemeContext";
import { useTranslation } from "react-i18next";
import AvatarDropdown from "../components/AvatarDropdown";
import VersionBadge from "../components/VersionBadge";
import { AuthApi, type CurrentUser } from "../api";
import Deployments from "../pages/Deployments";
import QuickDeploy from "../pages/QuickDeploy";
import Settings from "../pages/Settings";

const SIDEBAR_COLLAPSED_KEY = "datamason:sidebar:collapsed";

function getSavedCollapsed(): boolean {
  try {
    const saved = localStorage.getItem(SIDEBAR_COLLAPSED_KEY);
    if (saved !== null) return saved === "true";
  } catch {
    /* localStorage 不可用时使用默认值 */
  }
  return false;
}

/**
 * 控制台外壳（移植自 WorkMason layouts/MainLayout）：
 * 左侧可折叠 Sidebar（分组导航 + 拖拽边 + 账户页脚）+ 右侧页面内容。
 */
export default function MainLayout({ user }: { user: CurrentUser }) {
  const navigate = useNavigate();
  const location = useLocation();
  const isMobile = useIsMobile();
  const { message } = AntApp.useApp();
  const { t } = useTranslation();
  const { isDark } = useTheme();
  const [collapsed, setCollapsed] = useState<boolean>(() => getSavedCollapsed());

  const persistCollapsed = useCallback((next: boolean) => {
    setCollapsed(next);
    try {
      localStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(next));
    } catch {
      /* 忽略持久化失败 */
    }
  }, []);

  // 切到移动端强制折叠；回到桌面恢复保存的偏好
  useEffect(() => {
    setCollapsed(isMobile ? true : getSavedCollapsed());
  }, [isMobile]);

  // 移动端导航到新页后自动收起抽屉
  useEffect(() => {
    if (isMobile) setCollapsed(true);
  }, [location.pathname, isMobile]);

  const selectedKey = location.pathname.startsWith("/settings")
    ? location.pathname.slice(1)
    : location.pathname.startsWith("/quick-deploy")
      ? "quick-deploy"
      : "deployments";
  const sections = buildConsoleNav(user, t);

  const onNavigate = (item: NavItem) => {
    navigate(`/${item.key}`);
  };

  const onLogout = async () => {
    try {
      await AuthApi.logout();
    } catch {
      /* 忽略：客户端也强制清 */
    }
    sessionStorage.removeItem("datamason_me");
    message.success(t("shell.logoutSuccess"));
    window.location.replace("/login");
  };

  const railCollapsed = collapsed && !isMobile;

  const brand = railCollapsed ? (
    <img
      src="/pwa-192.png"
      alt="DataMason"
      style={{
        height: 32,
        width: 32,
        maxWidth: 32,
        objectFit: "contain",
        display: "block",
        flexShrink: 0,
        borderRadius: 8,
      }}
    />
  ) : (
    <img
      src={isDark ? "/logo_name_dark.png" : "/logo_name.png"}
      alt="DataMason"
      style={{
        height: "auto",
        width: "80%",
        maxWidth: "80%",
        objectFit: "contain",
        display: "block",
        flexShrink: 0,
      }}
    />
  );

  const footer = (
    <div className={`dm-sidebar__account${railCollapsed ? " dm-sidebar__account--collapsed" : ""}`}>
      {!railCollapsed && <VersionBadge />}
      <AvatarDropdown user={user} compact={railCollapsed} onLogout={() => void onLogout()} />
    </div>
  );

  return (
    <div
      style={{
        height: "100dvh",
        display: "flex",
        flexDirection: "row",
        background: "var(--fn-bg-layout)",
        overflow: "hidden",
      }}
    >
      {isMobile && !collapsed && (
        <div
          onClick={() => persistCollapsed(true)}
          style={{ position: "fixed", inset: 0, background: "var(--fn-overlay)", zIndex: 99 }}
        />
      )}

      <div style={{ position: "relative", flexShrink: 0, alignSelf: "stretch", display: "flex", minHeight: 0 }}>
        <Sidebar
          sections={sections}
          selectedKey={selectedKey}
          collapsed={collapsed}
          onToggle={() => persistCollapsed(!collapsed)}
          onNavigate={onNavigate}
          brand={brand}
          footer={footer}
          isMobile={isMobile}
        />
        {!isMobile && (
          <RailEdgeControl expanded={!collapsed} onToggle={() => persistCollapsed(!collapsed)} side="end" />
        )}
      </div>

      <div style={{ flex: 1, minWidth: 0, minHeight: 0, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        {isMobile && <Header onToggle={() => persistCollapsed(!collapsed)} />}
        <div className="page-container" style={{ flex: 1, background: "var(--fn-bg-layout)" }}>
          <div className="page-content">
            <Routes>
              <Route path="/" element={<Navigate to="/deployments" replace />} />
              <Route path="/quick-deploy" element={<QuickDeploy />} />
              <Route path="/deployments" element={<Deployments />} />
              <Route path="/settings/*" element={<Settings />} />
              <Route path="*" element={<Navigate to="/deployments" replace />} />
            </Routes>
          </div>
        </div>
      </div>
    </div>
  );
}
