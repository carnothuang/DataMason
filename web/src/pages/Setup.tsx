import { useState } from "react";
import { Alert, App as AntApp, Button, Card, Form, Input } from "antd";
import { IdcardOutlined, LockOutlined, UserOutlined } from "@ant-design/icons";
import { AuthApi } from "../api";

/** M1 首次启动向导（界面版，DR-10）：浏览器内创建首个平台管理员账号。
 *  仅当系统尚未初始化（users 为空）可用，成功后跳登录页。 */
export default function Setup() {
  const { message } = AntApp.useApp();
  const [loading, setLoading] = useState(false);

  const onFinish = async (values: {
    username: string;
    password: string;
    display_name?: string;
  }) => {
    setLoading(true);
    try {
      await AuthApi.setup(values);
      message.success("管理员已创建，请登录");
      window.location.replace("/login");
    } catch (err) {
      message.error((err as Error).message);
    } finally {
      setLoading(false);
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
        style={{ width: 400, boxShadow: "0 10px 30px rgba(0,0,0,0.06)" }}
        title={
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 20 }}>🧱</span>
            <span>DataMason · 首次启动</span>
          </div>
        }
      >
        <Alert
          type="info"
          showIcon
          message="创建管理员账号"
          description="系统尚未初始化。请设置平台管理员账号，之后即可登录并部署你的第一个业务系统。"
          style={{ marginBottom: 16 }}
        />
        <Form layout="vertical" onFinish={onFinish}>
          <Form.Item
            label="用户名"
            name="username"
            rules={[{ required: true, message: "请输入用户名" }]}
          >
            <Input prefix={<UserOutlined />} placeholder="用户名" autoComplete="username" />
          </Form.Item>
          <Form.Item label="显示名" name="display_name">
            <Input prefix={<IdcardOutlined />} placeholder="显示名（可选）" />
          </Form.Item>
          <Form.Item
            label="密码"
            name="password"
            rules={[
              { required: true, message: "请输入密码" },
              { min: 6, message: "密码至少 6 位" },
            ]}
          >
            <Input.Password
              prefix={<LockOutlined />}
              placeholder="密码（至少 6 位）"
              autoComplete="new-password"
            />
          </Form.Item>
          <Form.Item style={{ marginBottom: 8 }}>
            <Button type="primary" htmlType="submit" block loading={loading}>
              创建并进入
            </Button>
          </Form.Item>
          <Button type="link" block onClick={() => window.location.replace("/login")}>
            已有账号，去登录
          </Button>
        </Form>
      </Card>
    </div>
  );
}
