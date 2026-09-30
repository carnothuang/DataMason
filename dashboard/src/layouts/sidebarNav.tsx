import {
  AppstoreOutlined,
  DatabaseOutlined,
  HistoryOutlined,
  RocketOutlined,
  RobotOutlined,
  TeamOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";
import type { TFunction } from "i18next";
import type { NavSection } from "./Sidebar";
import type { CurrentUser } from "../api";

/** 控制台侧栏导航：部署清单（所有人）；平台管理项（仅平台管理员）。 */
export function buildConsoleNav(user: CurrentUser, t: TFunction): NavSection[] {
  const sections: NavSection[] = [
    {
      items: [
        { key: "quick-deploy", label: t("nav.quickDeploy"), icon: <RocketOutlined /> },
        { key: "deployments", label: t("nav.deployments"), icon: <AppstoreOutlined /> },
      ],
    },
  ];
  if (user.is_platform_admin) {
    sections.push({
      groupKey: t("nav.management"),
      items: [
        { key: "settings/models", label: t("nav.settingsModels"), icon: <RobotOutlined /> },
        { key: "settings/connections", label: t("nav.settingsConnections"), icon: <DatabaseOutlined /> },
        { key: "settings/redis", label: t("nav.settingsRedis"), icon: <ThunderboltOutlined /> },
        { key: "settings/users", label: t("nav.settingsUsers"), icon: <TeamOutlined /> },
        { key: "settings/ai", label: t("nav.settingsAi"), icon: <HistoryOutlined /> },
      ],
    });
  }
  return sections;
}
