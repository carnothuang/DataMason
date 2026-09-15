import { useEffect, useState } from "react";
import {
  App,
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Space,
  Table,
  Tag,
} from "antd";
import { ApiOutlined, PlusOutlined, StarFilled } from "@ant-design/icons";
import { LlmModel, ModelsApi } from "../../api";

interface FormValues {
  name: string;
  base_url: string;
  model_name: string;
  temperature: number;
  api_key: string;
}

export default function ModelsTab() {
  const { message } = App.useApp();
  const [rows, setRows] = useState<LlmModel[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<LlmModel | null>(null);
  const [testing, setTesting] = useState<number | null>(null);
  const [form] = Form.useForm<FormValues>();

  const load = async () => {
    setLoading(true);
    try {
      setRows(await ModelsApi.list());
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
    form.setFieldsValue({ temperature: 0 });
    setOpen(true);
  };

  const openEdit = (m: LlmModel) => {
    setEditing(m);
    form.setFieldsValue({
      name: m.name,
      base_url: m.base_url,
      model_name: m.model_name,
      temperature: m.temperature,
      api_key: m.has_api_key ? "********" : "",
    });
    setOpen(true);
  };

  const submit = async () => {
    const values = await form.validateFields();
    try {
      if (editing) {
        await ModelsApi.update(editing.id, values);
        message.success("已保存");
      } else {
        await ModelsApi.create(values);
        message.success("模型已添加");
      }
      setOpen(false);
      load();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const testModel = async (id: number) => {
    setTesting(id);
    try {
      const r = await ModelsApi.test(id);
      if (r.ok) message.success("连接成功");
      else message.error(r.error || "连接失败");
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
          添加模型
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
          {
            title: "默认",
            dataIndex: "is_default",
            width: 70,
            render: (v: boolean) => (v ? <StarFilled style={{ color: "#faad14" }} /> : null),
          },
          { title: "名称", dataIndex: "name" },
          { title: "模型 ID", dataIndex: "model_name" },
          { title: "端点", dataIndex: "base_url", ellipsis: true },
          {
            title: "密钥",
            dataIndex: "has_api_key",
            width: 90,
            render: (v: boolean) => (v ? <Tag color="green">已配置</Tag> : <Tag>无</Tag>),
          },
          { title: "温度", dataIndex: "temperature", width: 70 },
          {
            title: "操作",
            width: 260,
            render: (_: unknown, m: LlmModel) => (
              <Space>
                <Button size="small" onClick={() => openEdit(m)}>
                  编辑
                </Button>
                <Button
                  size="small"
                  icon={<ApiOutlined />}
                  loading={testing === m.id}
                  onClick={() => testModel(m.id)}
                >
                  测试
                </Button>
                {!m.is_default && (
                  <>
                    <Button size="small" onClick={() => ModelsApi.setDefault(m.id).then(load)}>
                      设默认
                    </Button>
                    <Popconfirm
                      title="删除该模型？"
                      onConfirm={async () => {
                        await ModelsApi.remove(m.id);
                        message.success("已删除");
                        load();
                      }}
                    >
                      <Button size="small" danger>
                        删除
                      </Button>
                    </Popconfirm>
                  </>
                )}
              </Space>
            ),
          },
        ]}
      />

      <Modal
        title={editing ? "编辑模型" : "添加模型"}
        open={open}
        onOk={submit}
        onCancel={() => setOpen(false)}
        okText="保存"
        cancelText="取消"
        width={560}
      >
        <Form form={form} layout="vertical" style={{ marginTop: 12 }}>
          <Form.Item name="name" label="显示名称" rules={[{ required: true }]}>
            <Input placeholder="如：火山方舟" />
          </Form.Item>
          <Form.Item name="base_url" label="OpenAI 兼容端点" rules={[{ required: true }]}>
            <Input placeholder="https://ark.cn-beijing.volces.com/api/coding/v3" />
          </Form.Item>
          <Form.Item name="model_name" label="模型 ID / 名称">
            <Input placeholder="ark-code-latest" />
          </Form.Item>
          <Form.Item name="api_key" label="API Key">
            <Input.Password placeholder={editing ? "留空或保持 ******** 表示不修改" : "sk-... / ark-..."} />
          </Form.Item>
          <Form.Item name="temperature" label="temperature（建表要求 0 以保证确定性）">
            <InputNumber min={0} max={2} step={0.1} style={{ width: 160 }} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
