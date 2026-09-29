import { useEffect, useState } from "react";
import { App, Button, Card, Form, Input, InputNumber, Space, Switch, Tag } from "antd";
import { ApiOutlined } from "@ant-design/icons";
import { RedisApi, RedisConfig } from "../../api";

export default function RedisTab() {
  const { message } = App.useApp();
  const [form] = Form.useForm<RedisConfig>();
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; text: string } | null>(null);

  const load = async () => {
    const cfg = await RedisApi.get();
    form.setFieldsValue(cfg);
  };
  useEffect(() => {
    load().catch((e) => message.error(e.message));
  }, []);

  const save = async () => {
    const values = await form.validateFields();
    try {
      await RedisApi.save(values);
      message.success("Redis 配置已保存");
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const test = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      // 先保存再测试，确保后端用的是最新配置
      const values = await form.validateFields();
      await RedisApi.save(values);
      const r = await RedisApi.test();
      if (r.ok) {
        setTestResult({ ok: true, text: `连接成功，Redis ${r.version}` });
      } else {
        setTestResult({ ok: false, text: r.error || "连接失败" });
      }
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setTesting(false);
    }
  };

  const enabled = Form.useWatch("enabled", form);

  return (
    <Card style={{ maxWidth: 640 }}>
      <Form form={form} layout="vertical" initialValues={{ enabled: false, port: 6379, db: 0 }}>
        <Form.Item name="enabled" label="启用 Redis 加速" valuePropName="checked">
          <Switch checkedChildren="开" unCheckedChildren="关" />
        </Form.Item>
        <fieldset disabled={!enabled} style={{ border: "none", padding: 0, margin: 0, opacity: enabled ? 1 : 0.5 }}>
          <Space align="start">
            <Form.Item name="host" label="主机" rules={[{ required: enabled }]}>
              <Input placeholder="127.0.0.1" style={{ width: 200 }} />
            </Form.Item>
            <Form.Item name="port" label="端口" rules={[{ required: enabled }]}>
              <InputNumber min={1} max={65535} style={{ width: 120 }} />
            </Form.Item>
            <Form.Item name="db" label="数据库">
              <InputNumber min={0} max={15} style={{ width: 100 }} />
            </Form.Item>
          </Space>
          <Form.Item name="password" label="密码（无密码留空）">
            <Input.Password placeholder="留空表示无密码" style={{ width: 340 }} />
          </Form.Item>
        </fieldset>
        <Space>
          <Button type="primary" onClick={save}>
            保存
          </Button>
          <Button icon={<ApiOutlined />} loading={testing} onClick={test}>
            保存并测试连接
          </Button>
          {testResult && (
            <Tag color={testResult.ok ? "green" : "red"} style={{ fontSize: 13, padding: "2px 8px" }}>
              {testResult.text}
            </Tag>
          )}
        </Space>
      </Form>
    </Card>
  );
}
