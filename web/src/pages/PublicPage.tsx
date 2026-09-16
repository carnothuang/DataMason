import { useEffect, useState } from "react";
import { App, Button, Card, Form, Input, InputNumber, Result, Select, Space, Spin, Typography } from "antd";
import { CheckCircleOutlined, SendOutlined } from "@ant-design/icons";
import { PublicApi, PublicField, PublicPageMeta } from "../api";

const { Title, Text, Paragraph } = Typography;
const { TextArea } = Input;

interface Props {
  deploymentId: number;
  pageId: number;
  /** 工作台内嵌预览（成员查看公开页）时为 true：卡片化，不占整屏。 */
  embedded?: boolean;
}

/** M3-5 访客公开页（DR-04）：匿名可访问的 public_form，只能提交，看不到菜单与数据。
 *  提交走 CRUD 写路径（归属记系统、进 activity_logs）。同一组件供工作台成员预览与匿名入口复用。 */
export default function PublicPage({ deploymentId, pageId, embedded }: Props) {
  const { message } = App.useApp();
  const [meta, setMeta] = useState<PublicPageMeta | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);
  const [form] = Form.useForm();

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError("");
    setDone(false);
    PublicApi.page(deploymentId, pageId)
      .then((m) => alive && setMeta(m))
      .catch((e) => alive && setError((e as Error).message))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [deploymentId, pageId]);

  const onSubmit = async (values: Record<string, unknown>) => {
    setSubmitting(true);
    try {
      const payload: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(values)) {
        if (v !== undefined && v !== null && v !== "") payload[k] = v;
      }
      await PublicApi.submit(deploymentId, pageId, payload);
      message.success("提交成功，感谢你的反馈");
      form.resetFields();
      setDone(true);
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  const body = () => {
    if (loading) {
      return (
        <div style={{ textAlign: "center", padding: 40 }}>
          <Spin tip="加载表单…" />
        </div>
      );
    }
    if (error || !meta) {
      return (
        <Result status="warning" title="页面不可用" subTitle={error || "该公开页不存在或未开放"} />
      );
    }
    if (done && !embedded) {
      return (
        <Result
          status="success"
          icon={<CheckCircleOutlined />}
          title="提交成功"
          subTitle="你的内容已提交，感谢反馈。"
          extra={
            <Button type="primary" onClick={() => setDone(false)}>
              再填一份
            </Button>
          }
        />
      );
    }
    return (
      <Form
        form={form}
        layout="vertical"
        onFinish={onSubmit}
        requiredMark
        style={{ maxWidth: 560 }}
      >
        {meta.fields.map((f) => (
          <Form.Item
            key={f.name}
            name={f.name}
            label={f.biz_name}
            rules={f.nullable ? undefined : [{ required: true, message: `请填写${f.biz_name}` }]}
          >
            {renderControl(f)}
          </Form.Item>
        ))}
        <Form.Item>
          <Button type="primary" htmlType="submit" icon={<SendOutlined />} loading={submitting}>
            提交
          </Button>
        </Form.Item>
      </Form>
    );
  };

  const inner = (
    <>
      {meta && (embedded || !done) && (
        <>
          <Title level={embedded ? 5 : 3} style={{ marginTop: 0 }}>
            {meta.title || "公开提交"}
          </Title>
          <Paragraph type="secondary" style={{ marginBottom: 16 }}>
            {meta.biz_name} · 访客提交页
          </Paragraph>
        </>
      )}
      {body()}
    </>
  );

  if (embedded) {
    return <Card>{inner}</Card>;
  }
  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "#f5f5f5",
        padding: 24,
      }}
    >
      <Card style={{ width: 640, maxWidth: "100%" }}>{inner}</Card>
      <Space style={{ position: "fixed", bottom: 12, width: "100%", justifyContent: "center" }}>
        <Text type="secondary" style={{ fontSize: 12 }}>
          由 DataMason 生成 · 访客仅可提交，无法查看任何数据
        </Text>
      </Space>
    </div>
  );
}

function renderControl(f: PublicField) {
  const t = (f.type || "").toUpperCase();
  if (t.includes("BOOL")) {
    return (
      <Select
        allowClear
        placeholder="请选择"
        options={[
          { value: 1, label: "是" },
          { value: 0, label: "否" },
        ]}
      />
    );
  }
  if (t.includes("INT") || t.includes("DEC") || t.includes("NUM") || t.includes("FLOAT") || t.includes("DOUBLE")) {
    return <InputNumber style={{ width: "100%" }} placeholder="请输入数字" />;
  }
  if (t.includes("DATETIME") || t.includes("TIMESTAMP") || t.includes("DATE")) {
    return <Input placeholder="YYYY-MM-DD HH:MM:SS" />;
  }
  if (t === "TEXT" || (f.length != null && f.length > 255)) {
    return <TextArea rows={4} placeholder="请输入" />;
  }
  return <Input placeholder="请输入" maxLength={f.length ?? undefined} />;
}