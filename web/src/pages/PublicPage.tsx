import { useEffect, useMemo, useState } from "react";
import { App, Button, Card, Col, Form, Result, Row, Space, Spin, Typography } from "antd";
import { CheckCircleOutlined, SendOutlined } from "@ant-design/icons";
import { PublicApi, PublicField, PublicPageMeta } from "../api";
import { baseType, renderFormControl } from "../components/FormControlRenderer";

const { Title, Text, Paragraph } = Typography;

interface Props {
  deploymentId: number;
  pageId: number;
  /** 工作台内嵌预览（成员查看公开页）时为 true：卡片化，不占整屏。 */
  embedded?: boolean;
}

/** M3-5 访客公开页（DR-04）；0.13.0 M7-5 改为公开布局配置驱动（缺省时与升级前一致）。 */
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

  const fields = useMemo(() => {
    if (!meta) return [] as PublicField[];
    const visible = meta.fields.filter((f) => !f.hidden);
    const order = meta.layout?.order || [];
    const inOrder = order
      .map((n) => visible.find((f) => f.name === n))
      .filter((f): f is PublicField => !!f);
    const rest = visible.filter((f) => !order.includes(f.name));
    return [...inOrder, ...rest];
  }, [meta]);

  const columns = meta?.layout?.columns_per_row || 1;
  const span = columns > 1 ? Math.floor(24 / columns) : 24;

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

  const rulesFor = (f: PublicField) => {
    const rules: Record<string, unknown>[] = [];
    if (!f.nullable) rules.push({ required: true, message: `请填写${f.biz_name}` });
    if (baseType(f.type) === "VARCHAR" && f.length) {
      rules.push({ max: f.length, message: `长度不能超过 ${f.length} 个字符` });
    }
    if (f.control_type === "checkbox" && f.length) {
      rules.push({
        validator: (_r: unknown, v: unknown) => {
          const len = Array.isArray(v) ? JSON.stringify(v).length : 0;
          if (len > f.length!) {
            return Promise.reject(new Error(`已选内容过长（上限 ${f.length} 字符）`));
          }
          return Promise.resolve();
        },
      });
    }
    return rules;
  };

  const renderItem = (f: PublicField) => (
    <Col span={span} key={f.name}>
      <Form.Item
        name={f.name}
        label={f.biz_name}
        rules={rulesFor(f)}
        extra={f.hint ? <Text type="secondary">{f.hint}</Text> : undefined}
      >
        {renderFormControl({
          name: f.name,
          bizName: f.biz_name,
          type: f.type,
          length: f.length,
          isFk: false,
          controlType: f.control_type ?? null,
          timeGranularity: f.time_granularity ?? null,
          options: f.options,
          disabled: f.readonly,
          deploymentId,
          table: meta?.table || "",
        })}
      </Form.Item>
    </Col>
  );

  const groups = meta?.layout?.groups || [];
  const grouped = new Set<string>();
  for (const g of groups) for (const n of g.field_names) grouped.add(n);
  const ungrouped = fields.filter((f) => !grouped.has(f.name));

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
      <Form form={form} layout="vertical" onFinish={onSubmit} requiredMark style={{ maxWidth: 640 }}>
        {groups.map((g, gi) => {
          const items = fields.filter((f) => g.field_names.includes(f.name));
          if (!items.length) return null;
          return (
            <div key={`g-${gi}`}>
              <Text strong>{g.title}</Text>
              <Row gutter={16} style={{ marginTop: 8 }}>{items.map(renderItem)}</Row>
            </div>
          );
        })}
        <Row gutter={16}>{ungrouped.map(renderItem)}</Row>
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
      <Card style={{ width: 720, maxWidth: "100%" }}>{inner}</Card>
      <Space style={{ position: "fixed", bottom: 12, width: "100%", justifyContent: "center" }}>
        <Text type="secondary" style={{ fontSize: 12 }}>
          由 DataMason 生成 · 访客仅可提交，无法查看任何数据
        </Text>
      </Space>
    </div>
  );
}
