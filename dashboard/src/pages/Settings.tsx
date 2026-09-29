import { Tabs } from "antd";
import PageShell from "../layouts/PageShell";
import ModelsTab from "./settings/ModelsTab";
import ConnectionsTab from "./settings/ConnectionsTab";
import RedisTab from "./settings/RedisTab";
import UsersTab from "./settings/UsersTab";
import AiGenerationsTab from "./settings/AiGenerationsTab";

export default function Settings() {
  return (
    <PageShell title="设置">
      <Tabs
        defaultActiveKey="models"
        style={{ marginTop: -8 }}
        items={[
          { key: "models", label: "模型管理", children: <ModelsTab /> },
          { key: "connections", label: "数据连接", children: <ConnectionsTab /> },
          { key: "redis", label: "Redis 加速", children: <RedisTab /> },
          { key: "users", label: "账号管理", children: <UsersTab /> },
          { key: "ai", label: "AI 生成", children: <AiGenerationsTab /> },
        ]}
      />
    </PageShell>
  );
}
