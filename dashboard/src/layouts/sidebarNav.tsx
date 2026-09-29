import { AppstoreOutlined, SettingOutlined } from "@ant-design/icons";
import type { TFunction } from "i18next";
import type { NavSection } from "./Sidebar";
import type { CurrentUser } from "../api";

/** 控制台侧栏导航：部署清单（所有人）；设置（仅平台管理员）。 */
export function buildConsoleNav(user: CurrentUser, t: TFunction): NavSection[] {
  const sections: NavSection[] = [
    {
      items: [{ key: "deployments", label: t("nav.deployments"), icon: <AppstoreOutlined /> }],
    },
  ];
  if (user.is_platform_admin) {
    sections.push({
      groupKey: t("nav.management"),
      items: [{ key: "settings", label: t("nav.settings"), icon: <SettingOutlined /> }],
    });
  }
  return sections;
}
