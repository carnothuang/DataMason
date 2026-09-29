import { Navigate, Route, Routes } from "react-router-dom";
import PageShell from "../layouts/PageShell";
import ModelsTab from "./settings/ModelsTab";
import ConnectionsTab from "./settings/ConnectionsTab";
import RedisTab from "./settings/RedisTab";
import UsersTab from "./settings/UsersTab";
import AiGenerationsTab from "./settings/AiGenerationsTab";

export default function Settings() {
  return (
    <PageShell title="设置">
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
