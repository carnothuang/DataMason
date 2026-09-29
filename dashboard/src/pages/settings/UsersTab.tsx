import { useEffect, useState } from "react";
import {
  App,
  Button,
  Drawer,
  Form,
  Input,
  Space,
  Switch,
  Table,
  Tag,
} from "antd";
import { PlusOutlined } from "@ant-design/icons";
import { AuthApi, CurrentUser } from "../../api";

interface FormValues {
  username: string;
  display_name?: string;
  password: string;
  is_platform_admin: boolean;
}

export default function UsersTab() {
  const { message } = App.useApp();
  const [rows, setRows] = useState<CurrentUser[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<CurrentUser | null>(null);
  const [form] = Form.useForm<FormValues>();

  const load = async () => {
    setLoading(true);
    try {
      setRows(await AuthApi.listUsers());
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
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({ is_platform_admin: false });
    setOpen(true);
  };

  const openEdit = (u: CurrentUser) => {
    setEditing(u);
    // 编辑模式：用户名不可改；密码留空表示不修改
    form.setFieldsValue({
      username: u.username,
      display_name: u.display_name,
      password: "",
      is_platform_admin: u.is_platform_admin,
    });
    setOpen(true);
  };

  const submit = async () => {
    const values = await form.validateFields();
    try {
      if (editing) {
        const payload: Parameters<typeof AuthApi.updateUser>[1] = {
          display_name: values.display_name,
          is_platform_admin: values.is_platform_admin,
        };
        if (values.password) payload.password = values.password;
        await AuthApi.updateUser(editing.id, payload);
        message.success("已保存");
      } else {
        await AuthApi.createUser({
          username: values.username,
          password: values.password,
          display_name: values.display_name,
          is_platform_admin: values.is_platform_admin,
        });
        message.success("账号已创建");
      }
      setOpen(false);
      load();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  return (
    <>
      <Space style={{ marginBottom: 12 }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
          新建账号
        </Button>
        <Button onClick={load}>刷新</Button>
      </Space>
      <Table
        rowKey="id"
        loading={loading}
        dataSource={rows}
        pagination={false}
        size="middle"
        columns={[
          { title: "ID", dataIndex: "id", width: 70 },
          { title: "用户名", dataIndex: "username" },
          { title: "显示名", dataIndex: "display_name" },
          {
            title: "角色",
            dataIndex: "is_platform_admin",
            width: 120,
            render: (v: boolean) =>
              v ? <Tag color="orange">平台管理员</Tag> : <Tag>普通用户</Tag>,
          },
          {
            title: "操作",
            width: 120,
            render: (_: unknown, u: CurrentUser) => (
              <Button size="small" onClick={() => openEdit(u)}>
                编辑
              </Button>
            ),
          },
        ]}
      />

      <Drawer
        title={editing ? `编辑账号 · ${editing.username}` : "新建账号"}
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
          <Form.Item
            name="username"
            label="用户名"
            rules={[{ required: true, message: "请输入用户名" }]}
          >
            <Input placeholder="如：laomao" disabled={!!editing} />
          </Form.Item>
          <Form.Item name="display_name" label="显示名（可选）">
            <Input placeholder="如：老猫" />
          </Form.Item>
          <Form.Item
            name="password"
            label={editing ? "新密码（留空表示不修改）" : "密码"}
            rules={
              editing
                ? []
                : [{ required: true, message: "请输入密码" }]
            }
          >
            <Input.Password placeholder={editing ? "留空不修改" : "请输入密码"} autoComplete="new-password" />
          </Form.Item>
          <Form.Item name="is_platform_admin" label="平台管理员" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Drawer>
    </>
  );
}
