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
  Segmented,
  Select,
  Space,
  Spin,
  Steps,
  Table,
  Tag,
  Typography,
} from "antd";
import {
  Connection,
  DeployApi,
  DeployResult,
  LinkProposal,
  LinkTableInventory,
  Proposal,
} from "../../api";
import { LinkApi } from "../../api";

const { Text } = Typography;

interface Props {
  open: boolean;
  onClose: () => void;
  onDeployed: () => void;
}

type Mode = "create" | "link";
type Phase = "input" | "loading" | "proposal" | "deploying" | "done";

export default function DeployWizard({ open, onClose, onDeployed }: Props) {
  const { message } = App.useApp();
  const [form] = Form.useForm<{
    description: string;
    biz_name: string;
    seed: boolean;
    enhance: boolean;
  }>();
  const [phase, setPhase] = useState<Phase>("input");
  const [mode, setMode] = useState<Mode>("create");
  const [proposal, setProposal] = useState<Proposal | LinkProposal | null>(null);
  const [result, setResult] = useState<DeployResult | null>(null);
  const [error, setError] = useState("");
  // 后端长耗时（LLM 生成/落库）期间的已用时计时，让用户确认后端仍在工作
  const [elapsed, setElapsed] = useState(0);
  const working = phase === "loading" || phase === "deploying";

  // M2-7 直连模式状态：可直连连接 / 表清单 / 已选表 / AI 补标签开关
  const [conns, setConns] = useState<Connection[]>([]);
  const [inventory, setInventory] = useState<LinkTableInventory[]>([]);
  const [connId, setConnId] = useState<number>();
  const [tables, setTables] = useState<string[]>([]);
  const [selecting, setSelecting] = useState(false);

  useEffect(() => {
    if (!working) return;
    const started = Date.now();
    setElapsed(0);
    const timer = window.setInterval(() => {
      setElapsed(Math.floor((Date.now() - started) / 1000));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [working]);

  // 打开时拉可直连连接清单（登录即可见，非平台管理员也可用）
  useEffect(() => {
    if (!open) return;
    LinkApi.connections()
      .then(setConns)
      .catch((e) => message.error(e.message));
  }, [open]);

  // 选中连接后拉表清单
  useEffect(() => {
    if (!open || !connId) return;
    setTables([]);
    setInventory([]);
    LinkApi.inventory(connId)
      .then((rows) =>
        setInventory(
          [...rows].sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0)),
        ),
      )
      .catch((e) => message.error(e.message));
  }, [open, connId]);

  const reset = () => {
    form.resetFields();
    form.setFieldsValue({ seed: true, enhance: false });
    setPhase("input");
    setProposal(null);
    setResult(null);
    setError("");
    setConnId(undefined);
    setTables([]);
    setInventory([]);
  };

  const close = () => {
    reset();
    onClose();
  };

  const switchMode = (m: Mode) => {
    setMode(m);
    setTables([]);
    setConnId(undefined);
    setInventory([]);
    setError("");
    if (phase === "proposal") {
      setProposal(null);
      setPhase("input");
    }
  };

  const generate = async () => {
    const values = await form.validateFields();
    if (mode === "create") {
      setPhase("loading");
      setError("");
      try {
        const p = await DeployApi.preview(values.description);
        setProposal(p);
        if (!form.getFieldValue("biz_name")) {
          form.setFieldsValue({ biz_name: values.description.slice(0, 12) });
        }
        setPhase("proposal");
      } catch (e) {
        setError((e as Error).message);
        setPhase("input");
      }
      return;
    }
    // 直连模式：需连接 + 至少一张表
    if (!connId) {
      message.error("请先选择要接入的连接");
      return;
    }
    if (!tables.length) {
      message.error("请至少选择一张表");
      return;
    }
    setPhase("loading");
    setError("");
    try {
      const p = await LinkApi.preview({
        connection_id: connId,
        tables,
        purpose: values.description,
        enhance: values.enhance,
      });
      setProposal(p);
      if (!form.getFieldValue("biz_name")) {
        form.setFieldsValue({ biz_name: values.description.slice(0, 12) });
      }
      setPhase("proposal");
    } catch (e) {
      setError((e as Error).message);
      setPhase("input");
    }
  };

  const smartSelect = async () => {
    if (!connId) return;
    const values = await form.validateFields(["description"]);
    setSelecting(true);
    try {
      const r = await LinkApi.select(connId, values.description);
      setTables(r.selection);
      message.success(
        `从 ${r.total} 张表筛出 ${r.selection.length} 张相关表${
          r.triggered ? `（两段式选表：${r.trigger_threshold} 张以上触发）` : ""
        }`,
      );
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setSelecting(false);
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
        mode,
        connection_id: mode === "link" ? connId : undefined,
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
  const isLink = mode === "link";
  const linkProposal = (isLink ? proposal : null) as LinkProposal | null;

  return (
    <Modal
      title="部署一套系统"
      open={open}
      onCancel={close}
      width={760}
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
          <Spin tip={isLink ? "正在反向生成表结构…" : "正在设计表结构与界面…"} size="large">
            <div style={{ height: 40 }} />
          </Spin>
          <div style={{ marginTop: 4 }}>
            <Text type="secondary">
              后端正在生成结构，已用时 {elapsed} 秒
            </Text>
            <br />
            <Text type="secondary" style={{ fontSize: 12 }}>
              {isLink
                ? "直连模式从已有库反射真实结构，无需 LLM（若勾选补业务标签则调用模型）"
                : "简单需求约 30~90 秒，复杂需求可能更久，请勿关闭窗口"}
            </Text>
          </div>
        </div>
      )}

      {phase === "deploying" && (
        <div style={{ textAlign: "center", padding: "40px 0" }}>
          <Spin tip={isLink ? "登记元数据 → 生成页面 → 注册入口…" : "建库 → 建表 → 页面 → 演示数据 → 注册入口…"} size="large">
            <div style={{ height: 40 }} />
          </Spin>
          <div style={{ marginTop: 4 }}>
            <Text type="secondary">后端正在落库，已用时 {elapsed} 秒</Text>
          </div>
        </div>
      )}

      {(phase === "input" || phase === "loading") && (
        <div style={{ marginBottom: 16 }}>
          <Segmented
            block
            value={mode}
            onChange={(v) => switchMode(v as Mode)}
            disabled={generating}
            options={[
              { label: "数据从零建", value: "create" },
              { label: "接已有的库", value: "link" },
            ]}
          />
        </div>
      )}

      {(phase === "input" || phase === "loading" || phase === "proposal") && (
        <Form form={form} layout="vertical" initialValues={{ seed: true, enhance: false }}>
          <Form.Item
            name="description"
            label={isLink ? "一句话描述（用于智能选表与补标签）" : "一句话描述"}
            rules={[{ required: true, message: "请描述你要验证什么" }]}
          >
            <Input.TextArea
              rows={2}
              placeholder={
                isLink
                  ? "如：接已有的客户订单库，先看订单与客户，做管理界面"
                  : "如：做个留言板收集访客反馈，记录访客、内容、时间和处理状态"
              }
              disabled={phase !== "input"}
            />
          </Form.Item>
          <Form.Item name="biz_name" label="系统名称" rules={[{ required: true }]}>
            <Input placeholder="给这套系统起个名" disabled={phase !== "input"} maxLength={24} />
          </Form.Item>

          {isLink && phase === "input" && (
            <>
              <Form.Item label="接入的连接（M1 连接管理里登记，登录即可使用）"
                         style={{ marginBottom: 8 }}>
                <Select
                  placeholder="选择要接入的已有连接"
                  value={connId}
                  onChange={(v) => setConnId(v)}
                  options={conns.map((c) => ({
                    value: c.id,
                    label: `${c.name}（${c.dialect}）`,
                  }))}
                />
              </Form.Item>
              {!!connId && (
                <Form.Item label="要接入的表" style={{ marginBottom: 8 }}
                           extra="无单主键的表不可用于 CRUD（已禁用）；可按描述一键智能推荐相关表">
                  <Space direction="vertical" style={{ width: "100%" }}>
                    <Select
                      mode="multiple"
                      placeholder="选择要接入的表（可在左侧按描述智能推荐）"
                      value={tables}
                      onChange={(v) => setTables(v as string[])}
                      maxTagCount={8}
                      options={inventory.map((t) => ({
                        value: t.name,
                        disabled: !t.single_pk,
                        label: `${t.name}（${t.column_count} 列${
                          t.single_pk ? "" : "，无单主键不可用"
                        }${t.has_created_by ? "" : "，缺 created_by"}）`,
                      }))}
                    />
                    <Space>
                      <Button size="small" onClick={smartSelect} loading={selecting}>
                        按描述智能推荐
                      </Button>
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        表多时自动两段式选表，只挑相关表进提案
                      </Text>
                    </Space>
                  </Space>
                </Form.Item>
              )}
              <Form.Item name="enhance" valuePropName="checked" style={{ marginBottom: 8 }}>
                <Checkbox>让 AI 补业务标签（可选；直连不生成演示数据）</Checkbox>
              </Form.Item>
            </>
          )}

          {!isLink && (
            <Form.Item name="seed" valuePropName="checked">
              <Checkbox disabled={phase !== "input"}>就绪即带演示数据（可用工作台一键清空）</Checkbox>
            </Form.Item>
          )}
        </Form>
      )}

      {phase === "proposal" && isLink && linkProposal && (
        <>
          {linkProposal.link.no_pk_tables.length > 0 && (
            <Alert
              type="warning"
              showIcon
              style={{ marginBottom: 12 }}
              message={`以下表无单主键/不存在，已从提案剔除（MVP 仅支持单主键表）：${
                linkProposal.link.no_pk_tables.join("、")
              }`}
            />
          )}
          {linkProposal.link.ownership_missing.length > 0 && (
            <Alert
              type="warning"
              showIcon
              style={{ marginBottom: 12 }}
              message={`以下外部表缺少归属列 created_by，「我的记录」将降级为全量可见（DataMason 绝不修改外部库）：${
                linkProposal.link.ownership_missing.join("、")
              }`}
            />
          )}
          {!!linkProposal.link.enrich_failed && (
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 12 }}
              message={`AI 补业务标签失败：${linkProposal.link.enrich_failed}（已用英文表名兜底，可在工作台再改）`}
            />
          )}
        </>
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
                {isLink && !t.columns.some((c) => c.name === "created_by") && (
                  <Tag color="orange">缺 created_by · 降级</Tag>
                )}
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
          subTitle={`${result.biz_name} · 已接入 ${
            result.data_mode === "link" ? "已有库" : "新建独立库"
          } · ${result.table_count} 张表${
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