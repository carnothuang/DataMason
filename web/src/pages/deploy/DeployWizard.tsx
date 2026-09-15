import { useState } from "react";
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
} from "antd";
import { DeployApi, DeployResult, Proposal } from "../../api";

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

  return (
    <Modal
      title="部署一套系统"
      open={open}
      onCancel={close}
      width={720}
      footer={
        phase === "input" ? (
          <Space>
            <Button onClick={close}>取消</Button>
            <Button type="primary" onClick={generate} loading={phase === "loading"}>
              生成提案
            </Button>
          </Space>
        ) : phase === "proposal" ? (
          <Space>
            <Button onClick={() => setPhase("input")}>调整一下</Button>
            <Button type="primary" onClick={deploy}>
              就这么部署
            </Button>
          </Space>
        ) : phase === "done" ? (
          <Button type="primary" onClick={close}>
            完成
          </Button>
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
        </div>
      )}

      {phase === "deploying" && (
        <div style={{ textAlign: "center", padding: "40px 0" }}>
          <Spin tip="建库 → 建表 → 页面 → 演示数据 → 注册入口…" size="large">
            <div style={{ height: 40 }} />
          </Spin>
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
          ]}
        />
      )}
    </Modal>
  );
}
