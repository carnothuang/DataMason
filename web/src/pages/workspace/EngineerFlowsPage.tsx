import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Empty,
  Input,
  Popconfirm,
  Select,
  Space,
  Spin,
  Tag,
  Typography,
} from "antd";
import {
  ArrowDownOutlined,
  ArrowUpOutlined,
  DeleteOutlined,
  PlusOutlined,
  SaveOutlined,
} from "@ant-design/icons";
import {
  DeployApi,
  EngineerApi,
  EngineerPageCard,
  FlowApi,
  FlowSpec,
  FlowStepInput,
  TableBlueprint,
} from "../../api";

const { Text } = Typography;

interface Props {
  deploymentId: number;
}

interface StepDraft {
  key: string;
  name: string;
  page_id: number | null;
  status_field: string | null;
  status_value: string;
}

let seq = 0;
const newStep = (): StepDraft => ({
  key: `step_${Date.now()}_${seq++}`,
  name: "",
  page_id: null,
  status_field: null,
  status_value: "",
});

/** M3-4 工程室「流程」编排（创建人独有 DR-14）：选已生成页面 + 业务动词步骤名 + 绑状态字段与完成条件。
 *  保存后自动生成 kind=flow 的流程页，可在「界面与菜单」拖入导航树（kind=flow）作为向导入口。 */
export default function EngineerFlowsPage({ deploymentId }: Props) {
  const { message } = App.useApp();
  const [flows, setFlows] = useState<FlowSpec[]>([]);
  const [pages, setPages] = useState<EngineerPageCard[]>([]);
  const [blueprints, setBlueprints] = useState<TableBlueprint[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const [editId, setEditId] = useState<number | null>(null);
  const [name, setName] = useState("");
  const [steps, setSteps] = useState<StepDraft[]>([]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [flowList, itf, bp] = await Promise.all([
        FlowApi.list(deploymentId),
        EngineerApi.interfaces(deploymentId),
        DeployApi.blueprint(deploymentId),
      ]);
      setFlows(flowList);
      setPages(itf.pages);
      setBlueprints(bp);
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [deploymentId, message]);

  useEffect(() => {
    void load();
  }, [load]);

  const pageById = useMemo(() => {
    const m = new Map<number, EngineerPageCard>();
    for (const p of pages) m.set(p.id, p);
    return m;
  }, [pages]);

  const colsByTable = useMemo(() => {
    const m = new Map<string, TableBlueprint>();
    for (const t of blueprints) m.set(t.name, t);
    return m;
  }, [blueprints]);

  // 步骤可选页面：已生成的业务页面（排除流程页，避免向导递归）
  const targetPages = useMemo(
    () => pages.filter((p) => p.kind !== "flow" && p.table),
    [pages],
  );

  const startNew = () => {
    setEditId(null);
    setName("");
    setSteps([newStep()]);
  };

  const editFlow = (f: FlowSpec) => {
    setEditId(f.id);
    setName(f.name);
    setSteps(
      f.steps.length
        ? f.steps.map((s, i) => ({
            key: `f${f.id}_${i}`,
            name: s.name,
            page_id: s.page_id,
            status_field: s.status_field,
            status_value: s.status_value || "",
          }))
        : [newStep()],
    );
  };

  const updateStep = (key: string, patch: Partial<StepDraft>) => {
    setSteps((prev) => prev.map((s) => (s.key === key ? { ...s, ...patch } : s)));
  };

  const moveStep = (idx: number, dir: -1 | 1) => {
    setSteps((prev) => {
      const next = [...prev];
      const j = idx + dir;
      if (j < 0 || j >= next.length) return prev;
      [next[idx], next[j]] = [next[j], next[idx]];
      return next;
    });
  };

  const save = async () => {
    const cleanName = name.trim();
    if (!cleanName) {
      message.error("请填写流程名称（如「入库流程」）");
      return;
    }
    if (steps.length === 0) {
      message.error("至少需要一个步骤");
      return;
    }
    const payload: FlowStepInput[] = [];
    for (let i = 0; i < steps.length; i++) {
      const s = steps[i];
      if (!s.name.trim()) {
        message.error(`第 ${i + 1} 步缺少步骤名（用业务动词，如「收货登记」）`);
        return;
      }
      if (!s.page_id) {
        message.error(`第 ${i + 1} 步「${s.name}」未选择页面`);
        return;
      }
      payload.push({
        name: s.name.trim(),
        page_id: s.page_id,
        done_when: { type: "records", min: 1 },
        status_field: s.status_field,
        status_value: s.status_value.trim() || null,
      });
    }
    setSaving(true);
    try {
      const saved = editId
        ? await FlowApi.update(deploymentId, editId, { name: cleanName, steps: payload })
        : await FlowApi.create(deploymentId, { name: cleanName, steps: payload });
      message.success(editId ? "流程已更新" : "流程已创建（流程页已生成，可在「界面与菜单」拖入导航树）");
      await load();
      editFlow(saved);
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const remove = async (f: FlowSpec) => {
    try {
      await FlowApi.remove(deploymentId, f.id);
      message.success("流程已删除（相关菜单一并清理）");
      if (editId === f.id) startNew();
      await load();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  if (loading && flows.length === 0 && pages.length === 0) {
    return (
      <div style={{ display: "flex", justifyContent: "center", padding: 48 }}>
        <Spin tip="加载流程…" />
      </div>
    );
  }

  return (
    <div>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="把「按步骤办事」砌进系统：一条流程 = 一串页面步骤（如 收货登记 → 质检 → 入库确认）。运行态给操作员一条步骤条，完成条件满足才可「下一步」，状态字段随步推进并写入操作日志。"
      />
      <div style={{ display: "flex", gap: 24, alignItems: "flex-start" }}>
        {/* 左：流程清单 */}
        <div
          style={{
            flex: "0 0 260px",
            border: "1px solid #f0f0f0",
            borderRadius: 8,
            padding: 12,
            background: "#fff",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
            <Text strong>流程清单</Text>
            <Button size="small" type="dashed" icon={<PlusOutlined />} onClick={startNew}>
              新建流程
            </Button>
          </div>
          {flows.length === 0 ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无流程" />
          ) : (
            <Space direction="vertical" style={{ width: "100%" }} size={6}>
              {flows.map((f) => (
                <div
                  key={f.id}
                  onClick={() => editFlow(f)}
                  style={{
                    cursor: "pointer",
                    padding: "6px 8px",
                    borderRadius: 6,
                    border: editId === f.id ? "1px solid #1677ff" : "1px solid #f0f0f0",
                    background: editId === f.id ? "#e6f4ff" : "#fff",
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    gap: 6,
                  }}
                >
                  <Text ellipsis style={{ minWidth: 0 }}>
                    {f.name}
                  </Text>
                  <Tag style={{ marginRight: 0 }}>{f.steps.length} 步</Tag>
                </div>
              ))}
            </Space>
          )}
        </div>

        {/* 右：编排 */}
        <div style={{ flex: 1, minWidth: 0 }}>
          <Card
            size="small"
            title={editId ? `编辑流程 #${editId}` : "新建流程"}
            extra={
              <Space>
                {editId && (
                  <Popconfirm
                    title="删除该流程？"
                    description="流程页与挂载它的菜单会一并清理。"
                    onConfirm={() => {
                      const f = flows.find((x) => x.id === editId);
                      if (f) void remove(f);
                    }}
                  >
                    <Button size="small" danger icon={<DeleteOutlined />} loading={saving}>
                      删除
                    </Button>
                  </Popconfirm>
                )}
                <Button type="primary" size="small" icon={<SaveOutlined />} loading={saving} onClick={save}>
                  保存
                </Button>
              </Space>
            }
          >
            <div style={{ marginBottom: 12 }}>
              <Text strong style={{ marginRight: 8 }}>
                流程名称
              </Text>
              <Input
                style={{ width: 260 }}
                placeholder="如：入库流程"
                value={name}
                maxLength={64}
                onChange={(e) => setName(e.target.value)}
              />
            </div>

            <Text type="secondary" style={{ fontSize: 12 }}>
              步骤名用业务动词（「收货登记」），选页面 + 绑状态字段；完成条件（v0）＝本步骤页面至少 1 条可推进记录。
            </Text>

            <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 10 }}>
              {steps.map((s, idx) => {
                const page = s.page_id ? pageById.get(s.page_id) : undefined;
                const cols = page?.table ? colsByTable.get(page.table)?.columns ?? [] : [];
                const fieldOpts = cols
                  .filter((c) => !c.is_ownership && !c.is_pk)
                  .map((c) => ({ value: c.name, label: c.biz_name || c.name }));
                return (
                  <Card key={s.key} size="small" type="inner" title={`第 ${idx + 1} 步`}>
                    <Space wrap style={{ marginBottom: 8 }}>
                      <Input
                        style={{ width: 180 }}
                        placeholder="步骤名（业务动词）"
                        value={s.name}
                        maxLength={64}
                        onChange={(e) => updateStep(s.key, { name: e.target.value })}
                      />
                      <Select
                        style={{ width: 220 }}
                        placeholder="选择页面"
                        value={s.page_id ?? undefined}
                        options={targetPages.map((p) => ({
                          value: p.id,
                          label: p.title || p.table_biz_name || p.table || `#${p.id}`,
                        }))}
                        onChange={(v) =>
                          updateStep(s.key, { page_id: v, status_field: null })
                        }
                      />
                      {page?.table && (
                        <>
                          <Select
                            style={{ width: 160 }}
                            placeholder="状态字段"
                            allowClear
                            value={s.status_field ?? undefined}
                            options={fieldOpts}
                            onChange={(v) => updateStep(s.key, { status_field: v ?? null })}
                          />
                          <Input
                            style={{ width: 140 }}
                            placeholder="推进为（如 待收货）"
                            value={s.status_value}
                            maxLength={64}
                            disabled={!s.status_field}
                            onChange={(e) => updateStep(s.key, { status_value: e.target.value })}
                          />
                        </>
                      )}
                      <Button
                        size="small"
                        icon={<ArrowUpOutlined />}
                        disabled={idx === 0}
                        onClick={() => moveStep(idx, -1)}
                      />
                      <Button
                        size="small"
                        icon={<ArrowDownOutlined />}
                        disabled={idx === steps.length - 1}
                        onClick={() => moveStep(idx, 1)}
                      />
                      <Button
                        size="small"
                        danger
                        icon={<DeleteOutlined />}
                        disabled={steps.length <= 1}
                        onClick={() => setSteps((prev) => prev.filter((x) => x.key !== s.key))}
                      />
                    </Space>
                    <div>
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        完成条件：本步骤页面至少 1 条记录推进为「{s.status_value || "（未绑定状态）"}」
                      </Text>
                    </div>
                  </Card>
                );
              })}
            </div>

            <Button
              style={{ marginTop: 12 }}
              type="dashed"
              icon={<PlusOutlined />}
              onClick={() => setSteps((prev) => [...prev, newStep()])}
            >
              添加步骤
            </Button>
          </Card>
        </div>
      </div>
    </div>
  );
}