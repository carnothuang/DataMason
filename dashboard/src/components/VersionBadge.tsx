import { Tooltip } from "antd";
import { useTranslation } from "react-i18next";
import pkg from "../../package.json";

/**
 * 当前版本胶囊（移植自 WorkMason components/CurrentVersionBadge）。
 * DataMason 无更新检查接口，版本号取自 package.json，仅作展示。
 */
export default function VersionBadge() {
  const { t } = useTranslation();
  const label = `v${pkg.version}`;

  return (
    <Tooltip title={t("account.currentVersion", { version: pkg.version })} mouseEnterDelay={0.35}>
      <span className="dm-version-badge">{label}</span>
    </Tooltip>
  );
}
