import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import PageShell from "../layouts/PageShell";
import ModelsTab from "./settings/ModelsTab";
import ConnectionsTab from "./settings/ConnectionsTab";
import RedisTab from "./settings/RedisTab";
import UsersTab from "./settings/UsersTab";
import AiGenerationsTab from "./settings/AiGenerationsTab";

export default function Settings() {
  const { t } = useTranslation();
  const location = useLocation();
  const section = location.pathname.split("/").filter(Boolean).pop() ?? "";

  const sectionMeta: Record<string, { title: string; subtitle: string }> = {
    models: { title: t("models.title"), subtitle: t("models.subtitle") },
    connections: { title: t("connections.title"), subtitle: t("connections.subtitle") },
    redis: { title: t("redis.title"), subtitle: t("redis.subtitle") },
    users: { title: t("users.title"), subtitle: t("users.subtitle") },
    ai: { title: t("ai.title"), subtitle: t("ai.subtitle") },
  };
  const meta = sectionMeta[section] ?? { title: t("nav.settings"), subtitle: "" };

  return (
    <PageShell title={meta.title} subtitle={meta.subtitle || undefined}>
      <Routes>
        <Route index element={<Navigate to="models" replace />} />
        <Route path="models" element={<ModelsTab />} />
        <Route path="connections" element={<ConnectionsTab />} />
        <Route path="redis" element={<RedisTab />} />
        <Route path="users" element={<UsersTab />} />
        <Route path="ai" element={<AiGenerationsTab />} />
        <Route path="*" element={<Navigate to="models" replace />} />
      </Routes>
    </PageShell>
  );
}
