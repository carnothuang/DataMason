import { useState } from "react";
import { Alert, App as AntApp, Button, Card, Divider, Form, Input, Typography } from "antd";
import { ApiOutlined, IdcardOutlined, LockOutlined, UserOutlined } from "@ant-design/icons";
import { AuthApi } from "../api";

const { Text } = Typography;

interface SetupValues {
  username: string;
  password: string;
  display_name?: string;
  llm_base_url?: string;
  llm_api_key?: string;
  llm_model?: string;
}

/** M1 首次启动向导（界面版，DR-10）：浏览器内创建首个平台管理员账号，
 *  可选同时登记默认 LLM 模型（端点/key/模型名）。仅系统未初始化可用。 */
export default function Setup() {
  const { message } = AntApp.useApp();
  const [loading, setLoading] = useState(false);

  const onFinish = async (values: SetupValues) => {
    setLoading(true);
    try {
      const llm = values.llm_base_url
        ? {
            name: "首次启动配置",
            base_url: values.llm_base_url,
            api_key: values.llm_api_key || "",
            model_name: values.llm_model || "",
          }
        : undefined;
      const r = await AuthApi.setup({
        username: values.username,
        password: values.password,
        display_name: values.display_name,
        llm,
      });
      message.success(
        r.llm_configured ? "管理员与 LLM 已配置，请登录" : "管理员已创建，请登录",
      );
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
        style={{ width: 440, boxShadow: "0 10px 30px rgba(0,0,0,0.06)" }}
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
          description="系统尚未初始化。设置平台管理员账号后即可登录部署业务系统；如已有模型端点，可一并填写。"
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

          <Divider plain style={{ margin: "8px 0 12px" }}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              LLM 模型（可选，也可稍后在「设置 → 模型管理」配置）
            </Text>
          </Divider>
          <Form.Item label="模型端点" name="llm_base_url">
            <Input
              prefix={<ApiOutlined />}
              placeholder="如 https://ark.cn-beijing.volces.com/api/coding/v3"
            />
          </Form.Item>
          <Form.Item label="模型名" name="llm_model">
            <Input placeholder="如 ark-code-latest" />
          </Form.Item>
          <Form.Item label="API Key" name="llm_api_key">
            <Input.Password placeholder="sk-..." autoComplete="off" />
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
