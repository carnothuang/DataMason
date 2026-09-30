import { useState } from "react";
import { ArrowRightOutlined } from "@ant-design/icons";
import { useTranslation } from "react-i18next";
import { Button } from "antd";
import PageShell from "../layouts/PageShell";
import DeployWizard from "./deploy/DeployWizard";
import "./QuickDeploy.css";

type BuilderMode = "auto" | "hybrid" | "manual";

const MASCOTS: Record<BuilderMode, string> = {
  auto: "/datamason-deployment-rapid.png",
  hybrid: "/datamason-deployment-hybrid.png",
  manual: "/datamason-deployment-professional.png",
};

export default function QuickDeploy() {
  const { t } = useTranslation();
  const [wizardOpen, setWizardOpen] = useState(false);
  const [builder, setBuilder] = useState<BuilderMode>("auto");

  const start = (mode: BuilderMode) => {
    setBuilder(mode);
    setWizardOpen(true);
  };

  const options: BuilderMode[] = ["auto", "hybrid", "manual"];

  return (
    <PageShell title={t("quick.title")} subtitle={t("quick.subtitle")}>
      <div className="dm-quick-grid">
        {options.map((mode) => (
          <div key={mode} className="dm-quick-card">
            <div className="dm-quick-card__media">
              <img
                src={MASCOTS[mode]}
                alt={t(`quick.${mode}Title`)}
                className="dm-quick-card__mascot"
                loading="lazy"
              />
            </div>
            <div className="dm-quick-card__body">
              <div className="dm-quick-card__title">{t(`quick.${mode}Title`)}</div>
              <div className="dm-quick-card__desc">{t(`quick.${mode}Desc`)}</div>
              <Button
                type="primary"
                block
                className="dm-quick-card__btn"
                onClick={() => start(mode)}
              >
                {t("quick.start")} <ArrowRightOutlined />
              </Button>
            </div>
          </div>
        ))}
      </div>

      <DeployWizard
        open={wizardOpen}
        initialBuilder={builder}
        onClose={() => setWizardOpen(false)}
        onDeployed={() => undefined}
      />
    </PageShell>
  );
}
