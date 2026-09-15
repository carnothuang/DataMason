import { Tabs } from "antd";
import ModelsTab from "./settings/ModelsTab";
import ConnectionsTab from "./settings/ConnectionsTab";
import RedisTab from "./settings/RedisTab";
import UsersTab from "./settings/UsersTab";

export default function Settings() {
  return (
    <Tabs
      defaultActiveKey="models"
      items={[
        { key: "models", label: "模型管理", children: <ModelsTab /> },
        { key: "connections", label: "数据连接", children: <ConnectionsTab /> },
        { key: "redis", label: "Redis 加速", children: <RedisTab /> },
        { key: "users", label: "账号管理", children: <UsersTab /> },
      ]}
    />
  );
}
