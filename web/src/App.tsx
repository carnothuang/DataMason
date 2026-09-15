import { Layout, Menu } from "antd";
import { AppstoreOutlined, SettingOutlined } from "@ant-design/icons";
import { Routes, Route, Navigate, useNavigate, useLocation } from "react-router-dom";
import Deployments from "./pages/Deployments";
import Settings from "./pages/Settings";

const { Header, Sider, Content } = Layout;

export default function App() {
  const navigate = useNavigate();
  const location = useLocation();
  const selected = location.pathname.startsWith("/settings") ? "settings" : "deployments";

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Sider theme="light" width={208} style={{ borderRight: "1px solid #f0f0f0" }}>
        <div style={{ height: 56, display: "flex", alignItems: "center", paddingLeft: 20, fontWeight: 700, fontSize: 16 }}>
          🧱 DataMason
        </div>
        <Menu
          mode="inline"
          selectedKeys={[selected]}
          onClick={(e) => navigate(e.key === "settings" ? "/settings" : "/deployments")}
          items={[
            { key: "deployments", icon: <AppstoreOutlined />, label: "部署清单" },
            { key: "settings", icon: <SettingOutlined />, label: "设置" },
          ]}
          style={{ borderInlineEnd: "none" }}
        />
      </Sider>
      <Layout>
        <Header style={{ background: "#fff", paddingInline: 24, borderBottom: "1px solid #f0f0f0" }}>
          <span style={{ fontWeight: 600 }}>数据瓦匠 · 工作台</span>
        </Header>
        <Content style={{ padding: 24 }}>
          <Routes>
            <Route path="/" element={<Navigate to="/deployments" replace />} />
            <Route path="/deployments" element={<Deployments />} />
            <Route path="/settings/*" element={<Settings />} />
          </Routes>
        </Content>
      </Layout>
    </Layout>
  );
}
