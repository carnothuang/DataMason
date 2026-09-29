/** M6-4 混合建库向导（四步：意图 → 骨架 → 逐表字段 → 全量确认）。
 *
 * 草稿单一数据源（DR-38）：全程 edit 同一份 HybridDraft，画布为视图；
 * 最后 merge 成完整 spec_json 后复用既有 POST /deployments/confirm 落库（M6-5）。
 * 仅创建模式可用；直连（link）无入口（DR-39）。
 */
import { useEffect, useMemo, useReducer, useState } from "react";
import {
  Alert,
  App,
  Button,
  Checkbox,
  Form,
  Input,
  Segmented,
  Select,
  Space,
  Steps,
  Tag,
  Typography,
} from "antd";
import {
  Connection,
  DeployApi,
  DeployResult,
  MergeIssue,
  SkeletonApi,
  SkeletonColumn,
} from "../../api";
import ErCanvas from "../../components/ErCanvas";
import MaximizableModal from "../../components/MaximizableModal";
import {
  HybridDraft,
  draftFromSkeleton,
  hybridReducer,
  toColumnsMap,
  toSkeletonJson,
  unguidedTables,
} from "./hybridDraft";

const { Text } = Typography;

const EMPTY_DRAFT: HybridDraft = { biz_notes: "", tables: [] };

interface Props {
  open: boolean;
  conns: Connection[];
  onClose: () => void;
  onDeployed: (result: DeployResult) => void;
}

export default function HybridBuilder({ open, conns, onClose, onDeployed }: Props) {
  const { message, modal } = App.useApp();
  const [form] = Form.useForm<{ description: string; biz_name: string; seed: boolean }>();
  const [step, setStep] = useState(0);
  const [draft, dispatch] = useReducer(hybridReducer, EMPTY_DRAFT);
  const [selected, setSelected] = useState<string | null>(null);
  const [layout, setLayout] = useState<"ring" | "grid">("ring");
  const [loading, setLoading] = useState(false);
  const [nl, setNl] = useState("");
  const [genMode, setGenMode] = useState<"overwrite" | "append">("overwrite");
  const [mergeErrors, setMergeErrors] = useState<MergeIssue[]>([]);
  const [targetConnId, setTargetConnId] = useState<number>();
  const [attempts, setAttempts] = useState(0);

  const unguided = useMemo(() => unguidedTables(draft), [draft]);
  const selectedTable = draft.tables.find((t) => t.name === selected) ?? null;

  // 刷新/关闭前的草稿丢失提示（DR-37：草稿不落库）
  useEffect(() => {
    if (!open || draft.tables.length === 0) return;
    const beforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, [open, draft.tables.length]);

  const close = () => {
    if (draft.tables.length) {
      modal.confirm({
        title: "放弃当前草稿？",
        content: "混合建库草稿只保存在浏览器内存，关闭后本次引导内容将丢失。",
        okText: "放弃草稿",
        okButtonProps: { danger: true },
        cancelText: "继续编辑",
        onOk: () => {
          resetAll();
          onClose();
        },
      });
      return;
    }
    resetAll();
    onClose();
  };

  const resetAll = () => {
    form.resetFields();
    form.setFieldsValue({ seed: true });
    dispatch({ type: "reset", draft: EMPTY_DRAFT });
    setStep(0);
    setSelected(null);
    setNl("");
    setMergeErrors([]);
    setAttempts(0);
    setTargetConnId(undefined);
  };

  const propose = async () => {
    const values = await form.validateFields();
    setLoading(true);
    setMergeErrors([]);
    try {
      const r = await SkeletonApi.propose(values.description, "create");
      dispatch({ type: "reset", draft: draftFromSkeleton(r.skeleton_json, r.columns) });
      setAttempts(r.attempts);
      if (!form.getFieldValue("biz_name")) {
        form.setFieldsValue({ biz_name: values.description.slice(0, 12) });
      }
      setSelected(r.skeleton_json.tables[0]?.name ?? null);
      setStep(1);
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const genField = async () => {
    if (!selectedTable) {
      message.error("请先在画布上选中一张表");
      return;
    }
    if (!nl.trim()) {
      message.error("请用一句话描述这张表需要哪些字段");
      return;
    }
    setLoading(true);
    setMergeErrors([]);
    try {
      const skeleton = toSkeletonJson(draft);
      const r = await SkeletonApi.columns(selectedTable.name, {
        skeleton,
        nl: nl.trim(),
        mode: genMode,
        existing_columns:
          genMode === "append"
            ? (toColumnsMap(draft)[selectedTable.name] as SkeletonColumn[])
            : undefined,
        guided_tables: draft.tables
          .filter((t) => t.columns.some((c) => !c.is_pk && !c.is_ownership))
          .map((t) => t.name),
      });
      dispatch({ type: "setColumns", table: selectedTable.name, columns: r.columns });
      message.success(`已为「${selectedTable.biz_name || selectedTable.name}」生成 ${r.columns.length} 个字段`);
      setNl("");
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const deploy = async () => {
    const values = await form.validateFields();
    // 未引导字段表拦截（M6-4-2）
    if (unguided.length) {
      const first = unguided[0];
      setSelected(first);
      setStep(2);
      message.error(`还有表没引导字段：${unguided.join("、")}，请补全后再落库`);
      return;
    }
    setLoading(true);
    setMergeErrors([]);
    try {
      const merged = await SkeletonApi.merge(
        toSkeletonJson(draft),
        toColumnsMap(draft) as Record<string, SkeletonColumn[]>,
      );
      const result = await DeployApi.confirm({
        spec_json: merged.spec_json,
        biz_name: values.biz_name,
        description: values.description,
        seed_data: values.seed,
        mode: "create",
        target_connection_id: targetConnId,
      });
      resetAll();
      onDeployed(result);
    } catch (e) {
      const err = e as Error & { errors?: MergeIssue[] };
      if (err.errors?.length) {
        setMergeErrors(err.errors);
        const firstTable = err.errors.find((x) => x.table)?.table;
        if (firstTable) setSelected(firstTable);
        setStep(2);
      }
      message.error(err.message);
    } finally {
      setLoading(false);
    }
  };

  const stepItems = [
    { title: "意图" },
    { title: "表骨架" },
    { title: "逐表字段" },
    { title: "全量确认" },
  ];

  const footer =
    step === 0 ? (
      <Space>
        <Button onClick={close} disabled={loading}>取消</Button>
        <Button type="primary" loading={loading} onClick={propose}>生成表骨架</Button>
      </Space>
    ) : step === 1 ? (
      <Space>
        <Button onClick={() => setStep(0)} disabled={loading}>上一步</Button>
        <Button type="primary" onClick={() => setStep(2)}>下一步：逐表字段</Button>
      </Space>
    ) : step === 2 ? (
      <Space>
        <Button onClick={() => setStep(1)} disabled={loading}>上一步</Button>
        <Button type="primary" onClick={() => setStep(3)}>下一步：全量确认</Button>
      </Space>
    ) : (
      <Space>
        <Button onClick={() => setStep(2)} disabled={loading}>回画布调整</Button>
        <Button type="primary" loading={loading} onClick={deploy}>合并并部署</Button>
      </Space>
    );

  return (
    <MaximizableModal
      title="混合建库（分步引导）"
      open={open}
      onCancel={close}
      width={1180}
      footer={footer}
      maskClosable={false}
      destroyOnClose
    >
      <Steps size="small" current={step} items={stepItems} style={{ margin: "4px 0 16px" }} />

      {attempts > 1 && (
        <Alert type="info" showIcon style={{ marginBottom: 12 }}
               message={`骨架经过 ${attempts} 次自检修正`} />
      )}
      {mergeErrors.length > 0 && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 12 }}
          message="合并校验未通过，请按下面定位回画布修正"
          description={
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {mergeErrors.map((e, i) => (
                <li key={i}>
                  {e.table ? `【${e.table}${e.column ? `.${e.column}` : ""}】` : ""}{e.message}
                </li>
              ))}
            </ul>
          }
        />
      )}

      <div style={{ display: step === 0 ? "block" : "none" }}>
        <Form form={form} layout="vertical" initialValues={{ seed: true }}>
          <Form.Item name="description" label="一句话描述你要建什么"
                     rules={[{ required: true, message: "请描述你要建什么" }]}>
            <Input.TextArea rows={2}
                            placeholder="如：客户与订单管理，含客户回访记录" />
          </Form.Item>
          <Form.Item name="biz_name" label="系统名称" rules={[{ required: true }]}>
            <Input placeholder="给这套系统起个名" maxLength={24} />
          </Form.Item>
          <Form.Item label="落库位置"
                     extra="默认本机独立 SQLite 库；也可选已登记的 MySQL/PostgreSQL 连接新建独立库（不碰已有库）">
            <Select
              allowClear
              placeholder="本机 SQLite（默认）"
              value={targetConnId}
              onChange={(v) => setTargetConnId(v)}
              options={conns.filter((c) => c.dialect !== "sqlite")
                .map((c) => ({ value: c.id, label: `${c.name}（${c.dialect}）` }))}
            />
          </Form.Item>
          <Form.Item name="seed" valuePropName="checked">
            <Checkbox>就绪即带演示数据（部署后可一键清空）</Checkbox>
          </Form.Item>
        </Form>
      </div>

      {step === 1 && (
        <>
          <Alert type="info" showIcon style={{ marginBottom: 10 }}
                 message="确认系统需要哪些表、表之间怎么关联；可加表/删表/改名/画线，线=外键关系。" />
          <ErCanvas draft={draft} dispatch={dispatch} selected={selected}
                    onSelect={setSelected} layout={layout} onLayoutChange={setLayout} />
        </>
      )}

      {step === 2 && (
        <>
          <Space style={{ marginBottom: 8 }} wrap>
            <Text type="secondary" style={{ fontSize: 12 }}>
              选中画布节点，用一句话告诉 AI 这张表要哪些字段：
            </Text>
            <Tag color={selectedTable ? "blue" : "default"}>
              {selectedTable ? `${selectedTable.biz_name || selectedTable.name}` : "未选表"}
            </Tag>
            <Segmented
              size="small"
              value={genMode}
              onChange={(v) => setGenMode(v as "overwrite" | "append")}
              options={[{ label: "整表重写", value: "overwrite" }, { label: "追加字段", value: "append" }]}
            />
          </Space>
          <Space.Compact style={{ width: "100%", marginBottom: 10 }}>
            <Input
              value={nl}
              onChange={(e) => setNl(e.target.value)}
              placeholder="如：客户表要记姓名、电话、VIP 等级、入会时间"
              onPressEnter={genField}
            />
            <Button type="primary" loading={loading} onClick={genField}>生成字段</Button>
          </Space.Compact>
          {unguided.length > 0 && (
            <Alert type="warning" showIcon style={{ marginBottom: 10 }}
                   message={`以下表还没有字段（落库前必须补全）：${unguided.join("、")}`} />
          )}
          <ErCanvas draft={draft} dispatch={dispatch} selected={selected}
                    onSelect={setSelected} height={520} layout={layout}
                    onLayoutChange={setLayout} />
        </>
      )}

      {step === 3 && (
        <div>
          <Alert
            type={unguided.length ? "warning" : "success"}
            showIcon
            style={{ marginBottom: 12 }}
            message={unguided.length
              ? `还有表没引导字段：${unguided.join("、")}（点「回画布调整」补全）`
              : `共 ${draft.tables.length} 张表，字段已就绪，可以落库`}
          />
          <Space direction="vertical" size={4} style={{ width: "100%" }}>
            {draft.tables.map((t) => (
              <Space key={t.name} size={8}>
                <Tag color="blue">{t.biz_name || t.name}</Tag>
                <code>{t.name}</code>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {t.columns.length} 列
                  {t.columns.some((c) => c.is_fk)
                    ? ` · 外键 ${t.columns.filter((c) => c.is_fk).length}`
                    : ""}
                </Text>
              </Space>
            ))}
          </Space>
        </div>
      )}
    </MaximizableModal>
  );
}
