import { useState, type ReactNode } from "react";
import { Avatar, Divider, Popover, Tag, Tooltip } from "antd";
import {
  DesktopOutlined,
  DownOutlined,
  GlobalOutlined,
  LogoutOutlined,
  MoonOutlined,
  SunOutlined,
  UpOutlined,
} from "@ant-design/icons";
import { useTranslation } from "react-i18next";
import { useTheme, type ThemePreference } from "../context/ThemeContext";
import LanguageSwitcher from "./LanguageSwitcher";
import type { CurrentUser } from "../api";

interface AvatarDropdownProps {
  user: CurrentUser;
  compact?: boolean;
  onLogout: () => void;
}

/**
 * 账户头像下拉（移植自 WorkMason components/AvatarDropdown）：
 * Popover 弹出账户菜单；展开态触发按钮为 头像+名字+箭头，折叠态为纯头像。
 * DataMason 无改密 / 更新检查等能力，菜单仅保留外观、语言、退出。
 */
export default function AvatarDropdown({ user, compact, onLogout }: AvatarDropdownProps) {
  const { t } = useTranslation();
  const { isDark, preference, setPreference } = useTheme();
  const [menuOpen, setMenuOpen] = useState(false);

  const initials = (user.display_name || "U").slice(0, 1).toUpperCase();

  const avatar = (
    <Avatar
      size={28}
      style={{ background: "var(--fn-color-brand-bg)", color: "var(--fn-text-brand)", fontSize: 13 }}
    >
      {initials}
    </Avatar>
  );

  const themeOptions: { key: ThemePreference; icon: ReactNode; label: string }[] = [
    { key: "light", icon: <SunOutlined />, label: t("account.themeLight") },
    { key: "dark", icon: <MoonOutlined />, label: t("account.themeDark") },
    { key: "system", icon: <DesktopOutlined />, label: t("account.themeSystem") },
  ];

  const menuContent = (
    <div className="dm-account-menu">
      <div className="dm-account-menu__header">
        <div className="dm-account-menu__header-top">
          <span className="dm-account-menu__name">{user.display_name}</span>
          {user.is_platform_admin && (
            <Tag color="blue" className="dm-account-menu__role">
              {t("account.admin")}
            </Tag>
          )}
        </div>
        <span className="dm-account-menu__handle">@{user.username}</span>
      </div>

      <Divider className="dm-account-menu__divider" />

      <div className="dm-account-menu__row">
        <span className="dm-account-menu__row-label">
          {isDark ? <MoonOutlined /> : <SunOutlined />}
          {t("account.appearance")}
        </span>
        <span className="dm-account-menu__seg">
          {themeOptions.map((option) => (
            <Tooltip key={option.key} title={option.label} mouseEnterDelay={0.4}>
              <button
                type="button"
                className={`dm-account-menu__seg-btn${preference === option.key ? " dm-account-menu__seg-btn--active" : ""}`}
                onClick={() => setPreference(option.key)}
                aria-label={option.label}
              >
                {option.icon}
              </button>
            </Tooltip>
          ))}
        </span>
      </div>

      <div className="dm-account-menu__row">
        <span className="dm-account-menu__row-label">
          <GlobalOutlined />
          {t("account.language")}
        </span>
        <LanguageSwitcher />
      </div>

      <Divider className="dm-account-menu__divider" />

      <button
        type="button"
        className="dm-account-menu__item dm-account-menu__item--danger"
        onClick={() => {
          setMenuOpen(false);
          onLogout();
        }}
      >
        <LogoutOutlined />
        <span>{t("account.logout")}</span>
      </button>
    </div>
  );

  const triggerButton = compact ? (
    <Tooltip title={user.display_name} placement="right" mouseEnterDelay={0.3}>
      <span role="button" tabIndex={0} className="dm-account-trigger-compact">
        {avatar}
      </span>
    </Tooltip>
  ) : (
    <button type="button" className="dm-account-trigger">
      {avatar}
      <span className="dm-account-trigger__name">{user.display_name}</span>
      {menuOpen ? <UpOutlined className="dm-account-trigger__chevron" /> : <DownOutlined className="dm-account-trigger__chevron" />}
    </button>
  );

  return (
    <Popover
      content={menuContent}
      trigger="click"
      open={menuOpen}
      onOpenChange={setMenuOpen}
      placement="topLeft"
      arrow={false}
      overlayClassName="dm-account-popover"
      destroyOnHidden
      getPopupContainer={() => document.body}
    >
      {triggerButton}
    </Popover>
  );
}
