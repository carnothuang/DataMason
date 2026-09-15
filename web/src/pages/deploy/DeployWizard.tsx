import { useEffect, useState } from "react";
import {
  Alert,
  App,
  Button,
  Checkbox,
  Descriptions,
  Divider,
  Form,
  Input,
  Modal,
  Result,
  Space,
  Spin,
  Steps,
  Table,
  Tag,
  Typography,
} from "antd";
import { DeployApi, DeployResult, Proposal } from "../../api";

const { Text } = Typography;

interface Props {
  open: boolean;
  onClose: () => void;
  onDeployed: () => void;
}

type Phase = "input" | "loading" | "proposal" | "deploying" | "done";

export default function DeployWizard({ open, onClose, onDeployed }: Props) {
  const { message } = App.useApp();
  const [form] = Form.useForm<{ description: string; biz_name: string; seed: boolean }>();
  const [phase, setPhase] = useState<Phase>("input");
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [result, setResult] = useState<DeployResult | null>(null);
  const [error, setError] = useState("");
  // 后端长耗时（LLM 生成/落库）期间的已用时计时，让用户确认后端仍在工作
  const [elapsed, setElapsed] = useState(0);
  const working = phase === "loading" || phase === "deploying";

  useEffect(() => {
    if (!working) return;
    const started = Date.now();
    setElapsed(0);
    const timer = window.setInterval(() => {
      setElapsed(Math.floor((Date.now() - started) / 1000));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [working]);

  const reset = () => {
    form.resetFields();
    form.setFieldsValue({ seed: true });
    setPhase("input");
    setProposal(null);
    setResult(null);
    setError("");
  };

  const close = () => {
    reset();
    onClose();
  };

  const generate = async () => {
    const values = await form.validateFields();
    setPhase("loading");
    setError("");
    try {
      const p = await DeployApi.preview(values.description);
      setProposal(p);
      if (!form.getFieldValue("biz_name")) {
        form.setFieldsValue({
          biz_name: values.description.slice(0, 12),
        });
      }
      setPhase("proposal");
    } catch (e) {
      setError((e as Error).message);
      setPhase("input");
    }
  };

  const deploy = async () => {
    if (!proposal) return;
    const values = await form.validateFields();
    setPhase("deploying");
    try {
      const r = await DeployApi.confirm({
        spec_json: proposal.spec_json,
        biz_name: values.biz_name,
        description: values.description,
        seed_data: values.seed,
      });
      setResult(r);
      setPhase("done");
      onDeployed();
    } catch (e) {
      message.error((e as Error).message);
      setPhase("proposal");
    }
  };

  const currentStep =
    phase === "input" || phase === "loading" ? 0 : phase === "proposal" || phase === "deploying" ? 1 : 2;
  const generating = phase === "loading";
  const deploying = phase === "deploying";

  return (
    <Modal
      title="部署一套系统"
      open={open}
      onCancel={close}
      width={720}
      footer={
        phase === "input" || generating ? (
          <Space>
            <Button onClick={close} disabled={generating}>取消</Button>
            <Button type="primary" onClick={generate} loading={generating}>
              生成提案
            </Button>
          </Space>
        ) : phase === "proposal" || deploying ? (
          <Space>
            <Button onClick={() => setPhase("input")} disabled={deploying}>调整一下</Button>
            <Button type="primary" onClick={deploy} loading={deploying}>
              就这么部署
            </Button>
          </Space>
        ) : phase === "done" && result ? (
          <Space>
            <Button onClick={close}>完成</Button>
            <Button type="primary" onClick={() => window.location.assign(result.entry_path)}>
              进入工作台
            </Button>
          </Space>
        ) : null
      }
    >
      <Steps
        size="small"
        current={currentStep}
        style={{ margin: "8px 0 20px" }}
        items={[{ title: "你要验证什么" }, { title: "系统设计提案" }, { title: "就绪" }]}
      />

      {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 12 }} />}

      {phase === "loading" && (
        <div style={{ textAlign: "center", padding: "40px 0" }}>
          <Spin tip="正在设计表结构与界面…" size="large">
            <div style={{ height: 40 }} />
          </Spin>
          <div style={{ marginTop: 4 }}>
            <Text type="secondary">
              后端正在调用 LLM 生成结构，已用时 {elapsed} 秒
            </Text>
            <br />
            <Text type="secondary" style={{ fontSize: 12 }}>
              简单需求约 30~90 秒，复杂需求可能更久，请勿关闭窗口；仅在后端明确失败时才会提示错误
            </Text>
          </div>
        </div>
      )}

      {phase === "deploying" && (
        <div style={{ textAlign: "center", padding: "40px 0" }}>
          <Spin tip="建库 → 建表 → 页面 → 演示数据 → 注册入口…" size="large">
            <div style={{ height: 40 }} />
          </Spin>
          <div style={{ marginTop: 4 }}>
            <Text type="secondary">后端正在落库，已用时 {elapsed} 秒</Text>
          </div>
        </div>
      )}

      {(phase === "input" || phase === "loading" || phase === "proposal") && (
        <Form form={form} layout="vertical" initialValues={{ seed: true }}>
          <Form.Item
            name="description"
            label="一句话描述"
            rules={[{ required: true, message: "请描述你要验证什么" }]}
          >
            <Input.TextArea
              rows={2}
              placeholder="如：做个留言板收集访客反馈，记录访客、内容、时间和处理状态"
              disabled={phase !== "input"}
            />
          </Form.Item>
          <Form.Item name="biz_name" label="系统名称" rules={[{ required: true }]}>
            <Input placeholder="给这套系统起个名" disabled={phase !== "input"} maxLength={24} />
          </Form.Item>
          <Form.Item name="seed" valuePropName="checked">
            <Checkbox disabled={phase !== "input"}>就绪即带演示数据（可用工作台一键清空）</Checkbox>
          </Form.Item>
        </Form>
      )}

      {phase === "proposal" && proposal && (
        <>
          {proposal.attempts > 1 && (
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 12 }}
              message={`结构经过 ${proposal.attempts} 次自检修正`}
            />
          )}
          <Descriptions size="small" column={1} style={{ marginBottom: 8 }}>
            <Descriptions.Item label="我准备这样建">{proposal.biz_notes}</Descriptions.Item>
          </Descriptions>

          {proposal.tables.map((t) => (
            <div key={t.name} style={{ marginBottom: 12 }}>
              <Space>
                <b>{t.biz_name}</b>
                <Tag>{t.name}</Tag>
              </Space>
              <Table
                size="small"
                rowKey="name"
                pagination={false}
                style={{ marginTop: 6 }}
                dataSource={t.columns}
                columns={[
                  { title: "列", dataIndex: "biz_name", render: (v, r) => v || r.name },
                  {
                    title: "字段",
                    dataIndex: "name",
                    render: (v: string, r) => (
                      <Space size={4}>
                        <code>{v}</code>
                        {r.is_pk && <Tag color="gold">主键</Tag>}
                        {r.is_fk && <Tag color="blue">外键→{r.ref_table}</Tag>}
                      </Space>
                    ),
                  },
                  { title: "类型", dataIndex: "type", width: 100 },
                ]}
              />
            </div>
          ))}

          <Divider style={{ margin: "8px 0" }} />
          <div>
            <span style={{ color: "#888", marginRight: 8 }}>生成界面：</span>
            {proposal.pages.map((p) => (
              <Tag key={`${p.table}-${p.kind}`} color={p.access === "public" ? "green" : "default"}>
                {p.title}（{p.access === "public" ? "公开" : "成员"}）
              </Tag>
            ))}
          </div>
        </>
      )}

      {phase === "done" && result && (
        <Result
          status="success"
          title="系统已就绪"
          subTitle={`${result.biz_name} · ${result.table_count} 张表${
            result.seeded ? " · 已带演示数据" : ""
          }`}
          extra={[
            <Tag key="entry" color="blue" style={{ fontFamily: "monospace", padding: "4px 10px" }}>
              {result.entry_path}
            </Tag>,
            <Button key="go" type="primary" onClick={() => window.location.assign(result.entry_path)}>
              进入工作台
            </Button>,
          ]}
        />
      )}
    </Modal>
  );
}
