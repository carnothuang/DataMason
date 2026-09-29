import { useEffect, useState } from "react";
import {
  App,
  Button,
  Drawer,
  Form,
  Input,
  InputNumber,
  Popconfirm,
  Radio,
  Space,
  Table,
  Tag,
} from "antd";
import { ApiOutlined, PlusOutlined } from "@ant-design/icons";
import { Connection, ConnectionsApi } from "../../api";

interface FormValues {
  name: string;
  dialect: "sqlite" | "mysql" | "postgresql";
  db_path: string;
  host: string;
  port: number;
  user: string;
  password: string;
  database: string;
  create: boolean;
}

const STATUS_COLOR: Record<string, string> = {
  ok: "green",
  error: "red",
  unknown: "default",
};

export default function ConnectionsTab() {
  const { message } = App.useApp();
  const [rows, setRows] = useState<Connection[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [testing, setTesting] = useState<number | null>(null);
  const [form] = Form.useForm<FormValues>();
  const dialect = Form.useWatch("dialect", form);

  const load = async () => {
    setLoading(true);
    try {
      setRows(await ConnectionsApi.list());
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    load();
  }, []);

  const openCreate = () => {
    form.resetFields();
    form.setFieldsValue({
      dialect: "sqlite",
      db_path: "biz/myapp.db",
      host: "127.0.0.1",
      port: 3306,
      user: "root",
      create: true,
    });
    setOpen(true);
  };

  const submit = async () => {
    const values = await form.validateFields();
    try {
      await ConnectionsApi.create(values as unknown as Record<string, unknown>);
      message.success("连接已保存");
      setOpen(false);
      load();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const testConn = async (id: number) => {
    setTesting(id);
    try {
      const r = await ConnectionsApi.test(id);
      if (r.ok) message.success("连接成功");
      else message.error(r.error || "连接失败");
      load();
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setTesting(null);
    }
  };

  return (
    <>
      <Space style={{ marginBottom: 12 }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
          新增连接
        </Button>
        <Button onClick={load}>刷新</Button>
      </Space>
      <Table
        rowKey="id"
        loading={loading}
        dataSource={rows}
        pagination={false}
        columns={[
          { title: "名称", dataIndex: "name" },
          {
            title: "类型",
            dataIndex: "dialect",
            width: 120,
            render: (v: string) => <Tag>{v.toUpperCase()}</Tag>,
          },
          {
            title: "状态",
            dataIndex: "status",
            width: 100,
            render: (v: string) => (
              <Tag color={STATUS_COLOR[v] || "default"}>
                {v === "ok" ? "正常" : v === "error" ? "异常" : "未测试"}
              </Tag>
            ),
          },
          {
            title: "操作",
            width: 160,
            render: (_: unknown, c: Connection) => (
              <Space>
                <Button
                  size="small"
                  icon={<ApiOutlined />}
                  loading={testing === c.id}
                  onClick={() => testConn(c.id)}
                >
                  测试
                </Button>
                <Popconfirm
                  title="删除该连接？仅删除登记，不删除数据库文件。"
                  onConfirm={async () => {
                    await ConnectionsApi.remove(c.id);
                    load();
                  }}
                >
                  <Button size="small" danger>
                    删除
                  </Button>
                </Popconfirm>
              </Space>
            ),
          },
        ]}
      />

      <Drawer
        title="新增数据连接"
        open={open}
        onClose={() => setOpen(false)}
        width={520}
        footer={
          <Space style={{ justifyContent: "flex-end", width: "100%" }}>
            <Button onClick={() => setOpen(false)}>取消</Button>
            <Button type="primary" onClick={submit}>
              保存
            </Button>
          </Space>
        }
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="连接名称" rules={[{ required: true }]}>
            <Input placeholder="如：本地 MySQL / 客户项目库" />
          </Form.Item>
          <Form.Item name="dialect" label="数据库类型">
            <Radio.Group
              options={[
                { label: "SQLite（业务量小，直接建文件）", value: "sqlite" },
                { label: "MySQL", value: "mysql" },
                { label: "PostgreSQL", value: "postgresql" },
              ]}
              optionType="button"
              buttonStyle="solid"
            />
          </Form.Item>

          {dialect === "sqlite" ? (
            <>
              <Form.Item name="db_path" label="数据库文件（相对路径存于数据目录）">
                <Input placeholder="biz/myapp.db" />
              </Form.Item>
              <Form.Item name="create" label="保存时立即创建并连接">
                <Radio.Group
                  options={[
                    { label: "是，立即创建", value: true },
                    { label: "否，仅登记", value: false },
                  ]}
                />
              </Form.Item>
            </>
          ) : (
            <>
              <Space>
                <Form.Item name="host" label="主机">
                  <Input placeholder="127.0.0.1" style={{ width: 180 }} />
                </Form.Item>
                <Form.Item name="port" label="端口">
                  <InputNumber min={1} max={65535} style={{ width: 110 }} />
                </Form.Item>
              </Space>
              <Space>
                <Form.Item name="user" label="账号">
                  <Input style={{ width: 180 }} />
                </Form.Item>
                <Form.Item name="password" label="密码">
                  <Input.Password style={{ width: 180 }} />
                </Form.Item>
              </Space>
              <Form.Item name="database" label="数据库名（可留空，部署时再建）">
                <Input placeholder="留空连接到实例" />
              </Form.Item>
            </>
          )}
        </Form>
      </Drawer>
    </>
  );
}
