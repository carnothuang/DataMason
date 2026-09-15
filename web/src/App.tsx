import { useEffect, useState } from "react";
import { Layout, Menu, Spin, Typography, App as AntApp } from "antd";
import { AppstoreOutlined, LogoutOutlined, SettingOutlined, UserOutlined } from "@ant-design/icons";
import { Routes, Route, Navigate, useNavigate, useLocation } from "react-router-dom";
import Deployments from "./pages/Deployments";
import Settings from "./pages/Settings";
import Login, { loadCachedUser } from "./pages/Login";
import Workspace from "./pages/workspace/Workspace";
import { AuthApi, CurrentUser } from "./api";

const { Header, Sider, Content } = Layout;

export default function App() {
  const navigate = useNavigate();
  const location = useLocation();
  const { message } = AntApp.useApp();

  const [user, setUser] = useState<CurrentUser | null>(() => loadCachedUser());
  const [bootChecking, setBootChecking] = useState(true);

  useEffect(() => {
    let alive = true;
    AuthApi.me()
      .then((me) => {
        if (!alive) return;
        setUser(me);
        sessionStorage.setItem("datamason_me", JSON.stringify(me));
      })
      .catch(() => {
        if (!alive) return;
        setUser(null);
        sessionStorage.removeItem("datamason_me");
      })
      .finally(() => alive && setBootChecking(false));
    return () => {
      alive = false;
    };
  }, []);

  // 登录页单独渲染（不带侧栏）
  if (location.pathname === "/login") {
    if (bootChecking) return <SpinPage />;
    if (user) return <Navigate to="/deployments" replace />;
    return <Login />;
  }

  // 工作台运行时 /app/{id}：全屏渲染部署的菜单+页面（仍要求登录）
  const workspaceId = parseWorkspaceId(location.pathname);
  if (workspaceId !== null) {
    if (bootChecking) return <SpinPage />;
    if (!user) {
      const target = `/login?from=${encodeURIComponent(location.pathname)}`;
      return <Navigate to={target} replace />;
    }
    return <Workspace deploymentId={workspaceId} />;
  }

  // 其他页面：未登录跳登录页
  if (bootChecking) return <SpinPage />;
  if (!user) {
    // 用 location 携带来源路径（react-router v6 Navigate 不支持 state in `to`）
    const target = `/login?from=${encodeURIComponent(location.pathname)}`;
    return <Navigate to={target} replace />;
  }

  const selected = location.pathname.startsWith("/settings") ? "settings" : "deployments";

  const onLogout = async () => {
    try {
      await AuthApi.logout();
    } catch {
      /* 忽略：客户端也强制清 */
    }
    sessionStorage.removeItem("datamason_me");
    message.success("已退出");
    // 强制整页刷新，让 App 重新挂载（useEffect 重新跑 → me() 401 → 跳 /login）
    window.location.replace("/login");
  };

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Sider theme="light" width={208} style={{ borderRight: "1px solid #f0f0f0", display: "flex", flexDirection: "column" }}>
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
          style={{ borderInlineEnd: "none", flex: 1 }}
        />
        <div style={{ borderTop: "1px solid #f0f0f0", padding: 12 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
            <UserOutlined style={{ color: "#c2410c" }} />
            <Typography.Text style={{ fontWeight: 600 }}>
              {user.display_name}
            </Typography.Text>
            {user.is_platform_admin && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                管理员
              </Typography.Text>
            )}
          </div>
          <Menu
            mode="inline"
            selectable={false}
            onClick={onLogout}
            items={[{ key: "logout", icon: <LogoutOutlined />, label: "退出登录" }]}
            style={{ borderInlineEnd: "none" }}
          />
        </div>
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
            <Route path="*" element={<Navigate to="/deployments" replace />} />
          </Routes>
        </Content>
      </Layout>
    </Layout>
  );
}

function SpinPage() {
  return (
    <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center" }}>
      <Spin tip="加载中..." />
    </div>
  );
}

/** 从 /app/{id}… 提取部署 ID；非工作台路径返回 null。 */
function parseWorkspaceId(pathname: string): number | null {
  const m = pathname.match(/^\/app\/(\d+)/);
  if (!m) return null;
  const id = Number(m[1]);
  return Number.isInteger(id) && id > 0 ? id : null;
}
