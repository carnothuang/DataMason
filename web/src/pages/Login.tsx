import { useEffect, useState } from "react";
import { Button, Card, Form, Input, App as AntApp } from "antd";
import { LockOutlined, UserOutlined } from "@ant-design/icons";
import { useLocation } from "react-router-dom";
import { AuthApi, CurrentUser } from "../api";

export default function Login() {
  const location = useLocation();
  const { message } = AntApp.useApp();
  const [loading, setLoading] = useState<"login" | "bootstrap" | null>(null);
  const [needsSetup, setNeedsSetup] = useState<boolean | null>(null);
  const [bootUser, setBootUser] = useState<string>("");

  useEffect(() => {
    AuthApi.setupStatus()
      .then((s) => {
        setNeedsSetup(s.needs_setup);
        // 已配置过管理员时，预填一个常见用户名方便测试
        if (!s.needs_setup) setBootUser("laomao");
      })
      .catch(() => setNeedsSetup(false));
  }, []);

  // 来源页：未登录跳转时以 ?from= 携带（react-router v6 不支持 Navigate state in to）
  const params = new URLSearchParams(location.search);
  const from = params.get("from") || "/deployments";

  const onLogin = async (values: { username: string; password: string }) => {
    setLoading("login");
    try {
      const me = await AuthApi.login(values);
      sessionStorage.setItem("datamason_me", JSON.stringify(me));
      message.success(`欢迎，${me.display_name}`);
      // 用 location 强制整页刷新，让 App 重新挂载并跑 useEffect 调 me()
      // 避免 navigate 后 App 的 user state 仍为 null 又跳回 /login
      window.location.replace(from);
    } catch (err) {
      message.error((err as Error).message);
    } finally {
      setLoading(null);
    }
  };

  const onBootstrap = async () => {
    setLoading("bootstrap");
    try {
      const res = await AuthApi.bootstrap();
      if (res.skipped) {
        message.info("已存在管理员，无需初始化");
      } else if (Array.isArray(res.created) && res.created.length > 0) {
        message.success(`已创建管理员：${res.created.join(", ")}（请使用环境变量中的密码登录）`);
      } else {
        message.info("已处理");
      }
      setNeedsSetup(false);
    } catch (err) {
      message.error((err as Error).message);
    } finally {
      setLoading(null);
    }
  };

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "linear-gradient(135deg, #fff7ed 0%, #ffedd5 100%)",
      }}
    >
      <Card
        style={{ width: 380, boxShadow: "0 10px 30px rgba(0,0,0,0.06)" }}
        title={
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 20 }}>🧱</span>
            <span>DataMason · 登录</span>
          </div>
        }
      >
        <Form layout="vertical" onFinish={onLogin} initialValues={{ username: bootUser }}>
          <Form.Item
            label="用户名"
            name="username"
            rules={[{ required: true, message: "请输入用户名" }]}
          >
            <Input prefix={<UserOutlined />} placeholder="用户名" autoComplete="username" />
          </Form.Item>
          <Form.Item
            label="密码"
            name="password"
            rules={[{ required: true, message: "请输入密码" }]}
          >
            <Input.Password prefix={<LockOutlined />} placeholder="密码" autoComplete="current-password" />
          </Form.Item>
          <Form.Item style={{ marginBottom: 8 }}>
            <Button type="primary" htmlType="submit" block loading={loading === "login"}>
              登录
            </Button>
          </Form.Item>
          {needsSetup && (
            <>
              <Button type="link" block onClick={() => window.location.assign("/setup")}>
                首次启动：创建管理员账号
              </Button>
              <Button
                type="link"
                block
                onClick={onBootstrap}
                loading={loading === "bootstrap"}
              >
                或从环境变量初始化管理员
              </Button>
            </>
          )}
        </Form>
      </Card>
    </div>
  );
}

// 供 App 门卫使用：从 sessionStorage 取已登录用户
export function loadCachedUser(): CurrentUser | null {
  try {
    const raw = sessionStorage.getItem("datamason_me");
    if (!raw) return null;
    const parsed = JSON.parse(raw) as CurrentUser;
    if (parsed && typeof parsed.id === "number") return parsed;
    sessionStorage.removeItem("datamason_me");
    return null;
  } catch {
    return null;
  }
}
