import { useEffect, useMemo, useState } from "react";
import {
  App,
  Button,
  Col,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Radio,
  Row,
  Select,
  Space,
  Spin,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import {
  ArrowDownOutlined,
  ArrowUpOutlined,
  DeleteOutlined,
  PlusOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";
import type { FormConfigPayload, FormControlConfig, FormControlType, FormOptionItem } from "../../api";
import { EngineerApi, FormsApi } from "../../api";
import { CONTROL_LABELS, compatibleControls, renderFormControl } from "../../components/FormControlRenderer";

const { Text } = Typography;

interface DraftField {
  field_name: string;
  biz_name: string;
  data_type: string;
  length: number | null;
  nullable: boolean;
  is_fk: boolean;
  control_type: FormControlType | null;
  control_config: FormControlConfig;
  time_granularity: "date" | "datetime" | null;
  readonly: boolean;
  hidden: boolean;
  hint: string;
  options: FormOptionItem[];
  group_title: string | null;
}

interface Props {
  deploymentId: number;
  table: string;
  pageId: number;
  title: string;
  open: boolean;
  onClose(): void;
  onSaved(): void;
}

const MAX_OPTIONS = 200;

/** 表单配置器（0.13.0 M7-6）：控件类型 / 候选项 / 布局 / 实时预览；草稿仅内存，保存才落库。 */
export default function FormDesigner({
  deploymentId,
  table,
  pageId,
  title,
  open,
  onClose,
  onSaved,
}: Props) {
  const { message } = App.useApp();
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [degraded, setDegraded] = useState(false);
  const [draftTitle, setDraftTitle] = useState(title);
  const [fields, setFields] = useState<DraftField[]>([]);
  const [order, setOrder] = useState<string[]>([]);
  const [groups, setGroups] = useState<{ title: string }[]>([]);
  const [columns, setColumns] = useState<number>(1);
  const [optionField, setOptionField] = useState<string | null>(null);
  const [paramField, setParamField] = useState<string | null>(null);
  const [genText, setGenText] = useState("");
  const [generating, setGenerating] = useState(false);
  const [previewForm] = Form.useForm();

  useEffect(() => {
    if (!open) return;
    let alive = true;
    setLoading(true);
    setDegraded(false);
    setOptionField(null);
    setParamField(null);
    setGenText("");
    setDraftTitle(title);
    FormsApi.getConfig(deploymentId, table)
      .then((cfg) => {
        if (!alive) return;
        setDegraded(cfg.degraded);
        const fs: DraftField[] = cfg.fields
          .filter((f) => !f.is_pk && !f.is_ownership)
          .map((f) => ({
            field_name: f.field_name,
            biz_name: f.biz_name || f.field_name,
            data_type: f.data_type,
            length: f.length,
            nullable: f.nullable,
            is_fk: f.is_fk,
            control_type: f.configured ? f.control_type : null,
            control_config: { ...(f.control_config || {}) },
            time_granularity: f.time_granularity,
            readonly: f.readonly,
            hidden: f.hidden,
            hint: f.hint,
            options: f.options || [],
            group_title: null,
          }));
        const names = fs.map((f) => f.field_name);
        const groupDefs = cfg.layout.groups || [];
        for (const g of groupDefs) {
          for (const n of g.field_names) {
            const f = fs.find((x) => x.field_name === n);
            if (f) f.group_title = g.title;
          }
        }
        setGroups(groupDefs.map((g) => ({ title: g.title })));
        const ordered = (cfg.layout.order || []).filter((n) => names.includes(n));
        setOrder([...ordered, ...names.filter((n) => !ordered.includes(n))]);
        setColumns(cfg.layout.columns_per_row || 1);
        setFields(fs);
      })
      .catch((e) => alive && message.error((e as Error).message))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [open, deploymentId, table, title, message]);

  const orderedFields = useMemo(() => {
    const map = new Map(fields.map((f) => [f.field_name, f]));
    const ordered = order.map((n) => map.get(n)).filter((f): f is DraftField => !!f);
    const rest = fields.filter((f) => !order.includes(f.field_name));
    return [...ordered, ...rest];
  }, [fields, order]);

  const patch = (name: string, changes: Partial<DraftField>) =>
    setFields((prev) => prev.map((f) => (f.field_name === name ? { ...f, ...changes } : f)));

  const move = (name: string, dir: -1 | 1) => {
    setOrder((prev) => {
      const names = prev.length ? [...prev] : fields.map((f) => f.field_name);
      const i = names.indexOf(name);
      const j = i + dir;
      if (i < 0 || j < 0 || j >= names.length) return prev;
      [names[i], names[j]] = [names[j], names[i]];
      return names;
    });
  };

  const resetField = (name: string) =>
    patch(name, {
      control_type: null,
      control_config: {},
      time_granularity: null,
      readonly: false,
      hidden: false,
      hint: "",
      options: [],
    });

  const generateOptions = async (name: string) => {
    if (!genText.trim()) {
      message.warning("请先描述候选项的取值来源");
      return;
    }
    setGenerating(true);
    try {
      const res = await FormsApi.generateOptions(deploymentId, table, name, genText.trim());
      const field = fields.find((f) => f.field_name === name);
      const existing = field?.options || [];
      const seen = new Set(existing.map((o) => o.value));
      const merged = [...existing];
      for (const it of res.items) {
        if (!seen.has(it.value)) {
          seen.add(it.value);
          merged.push(it);
        }
      }
      patch(name, { options: merged.slice(0, MAX_OPTIONS) });
      setGenText("");
      message.success(`已生成 ${res.items.length} 项候选，请勾选/编辑后保存`);
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setGenerating(false);
    }
  };

  const validate = (): string | null => {
    for (const f of fields) {
      if (f.control_type === "radio" || f.control_type === "checkbox" || f.control_type === "cascader") {
        if (f.options.length > MAX_OPTIONS) return `「${f.biz_name}」候选项超过 ${MAX_OPTIONS} 个`;
        const seen = new Set<string>();
        for (const o of f.options) {
          if (!o.value.trim()) return `「${f.biz_name}」存在空的候选项存储值`;
          if (seen.has(o.value)) return `「${f.biz_name}」候选项存储值「${o.value}」重复`;
          seen.add(o.value);
          if (o.parent_value && o.parent_value === o.value)
            return `「${f.biz_name}」候选项「${o.value}」不能以自己为父级`;
        }
        if (f.control_type === "cascader") {
          const parentMap = new Map<string, string | null>();
          for (const o of f.options) parentMap.set(o.value, o.parent_value ?? null);
          for (const o of f.options) {
            if (o.parent_value && !parentMap.has(o.parent_value))
              return `「${f.biz_name}」候选项「${o.value}」的父级「${o.parent_value}」不存在`;
          }
          for (const start of parentMap.keys()) {
            const seenPath = new Set<string>();
            let cur: string | null | undefined = start;
            while (cur !== undefined && cur !== null) {
              if (seenPath.has(cur)) return `「${f.biz_name}」候选项层级存在循环引用`;
              seenPath.add(cur);
              cur = parentMap.get(cur);
            }
          }
        }
      }
      if (f.control_type === "slider") {
        const c = f.control_config;
        if (c?.min !== undefined && c.max !== undefined && Number(c.min) >= Number(c.max))
          return `「${f.biz_name}」滑块最小值必须小于最大值`;
        if (c?.step !== undefined && Number(c.step) <= 0)
          return `「${f.biz_name}」滑块步长必须为正数`;
      }
      if (f.control_type === "rate") {
        const c = f.control_config;
        if (c?.max_stars !== undefined && (Number(c.max_stars) < 1 || Number(c.max_stars) > 10))
          return `「${f.biz_name}」评分星数须在 1~10 之间`;
      }
    }
    if (groups.length > 20) return "分组最多 20 组";
    return null;
  };

  const submit = async () => {
    const err = validate();
    if (err) {
      message.error(err);
      return;
    }
    const payload: FormConfigPayload = {
      fields: fields.map((f) => ({
        field_name: f.field_name,
        control_type: f.control_type,
        control_config:
          f.control_type === "slider" || f.control_type === "rate" || f.control_type === "cascader"
            ? f.control_config
            : undefined,
        time_granularity:
          f.control_type === "date" || f.control_type === "datetime"
            ? f.time_granularity
            : null,
        readonly: f.readonly,
        hidden: f.hidden,
        hint: f.hint,
        options:
          f.control_type === "radio" || f.control_type === "checkbox" || f.control_type === "cascader"
            ? f.options
            : [],
      })),
      layout: {
        columns_per_row: columns,
        order: orderedFields.map((f) => ({ field_name: f.field_name })),
        groups: groups.map((g) => ({
          title: g.title,
          field_names: fields.filter((f) => f.group_title === g.title).map((f) => f.field_name),
        })),
      },
    };
    setSaving(true);
    try {
      if (draftTitle.trim() && draftTitle.trim() !== title) {
        await EngineerApi.updatePageTitle(deploymentId, pageId, draftTitle.trim());
      }
      await FormsApi.saveConfig(deploymentId, table, payload);
      message.success("表单配置已保存");
      onSaved();
      onClose();
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const optionFieldDraft = fields.find((f) => f.field_name === optionField) || null;
  const paramFieldDraft = fields.find((f) => f.field_name === paramField) || null;

  const columnsDef = [
    {
      title: "顺序",
      key: "order",
      width: 70,
      render: (_: unknown, f: DraftField) => (
        <Space size={0}>
          <Button
            type="text"
            size="small"
            icon={<ArrowUpOutlined />}
            onClick={() => move(f.field_name, -1)}
          />
          <Button
            type="text"
            size="small"
            icon={<ArrowDownOutlined />}
            onClick={() => move(f.field_name, 1)}
          />
        </Space>
      ),
    },
    {
      title: "字段",
      key: "field",
      width: 150,
      render: (_: unknown, f: DraftField) => (
        <div>
          <Text strong>{f.biz_name}</Text>
          <div>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {f.field_name} · {f.data_type}
            </Text>
          </div>
        </div>
      ),
    },
    {
      title: "控件",
      key: "control",
      width: 130,
      render: (_: unknown, f: DraftField) => (
        <Select
          size="small"
          style={{ width: "100%" }}
          allowClear
          placeholder="默认"
          value={f.control_type ?? undefined}
          options={compatibleControls(f.data_type, f.is_fk).map((c) => ({
            value: c,
            label: CONTROL_LABELS[c] || c,
          }))}
          onChange={(v) =>
            patch(f.field_name, {
              control_type: (v as FormControlType | undefined) ?? null,
              control_config:
                v === "slider" || v === "rate" || v === "cascader" ? f.control_config : {},
              time_granularity:
                v === "date" || v === "datetime"
                  ? f.time_granularity || (v as "date" | "datetime")
                  : null,
              options:
                v === "radio" || v === "checkbox" || v === "cascader" ? f.options : [],
            })
          }
        />
      ),
    },
    {
      title: "候选项",
      key: "options",
      width: 110,
      render: (_: unknown, f: DraftField) =>
        f.control_type === "radio" || f.control_type === "checkbox" || f.control_type === "cascader" ? (
          <Button size="small" onClick={() => setOptionField(f.field_name)}>
            配置（{f.options.length}）
          </Button>
        ) : (
          <Text type="secondary">—</Text>
        ),
    },
    {
      title: "参数",
      key: "params",
      width: 90,
      render: (_: unknown, f: DraftField) =>
        f.control_type === "slider" || f.control_type === "rate" || f.control_type === "cascader" ? (
          <Button size="small" onClick={() => setParamField(f.field_name)}>
            参数
          </Button>
        ) : (
          <Text type="secondary">—</Text>
        ),
    },
    {
      title: "只读",
      key: "readonly",
      width: 60,
      render: (_: unknown, f: DraftField) => (
        <Switch size="small" checked={f.readonly} onChange={(v) => patch(f.field_name, { readonly: v })} />
      ),
    },
    {
      title: "隐藏",
      key: "hidden",
      width: 60,
      render: (_: unknown, f: DraftField) => (
        <Switch size="small" checked={f.hidden} onChange={(v) => patch(f.field_name, { hidden: v })} />
      ),
    },
    {
      title: "分组",
      key: "group",
      width: 120,
      render: (_: unknown, f: DraftField) => (
        <Select
          size="small"
          style={{ width: "100%" }}
          allowClear
          placeholder="未分组"
          value={f.group_title ?? undefined}
          options={groups.map((g) => ({ value: g.title, label: g.title }))}
          onChange={(v) => patch(f.field_name, { group_title: v ?? null })}
        />
      ),
    },
    {
      title: "提示",
      key: "hint",
      render: (_: unknown, f: DraftField) => (
        <Input
          size="small"
          maxLength={200}
          value={f.hint}
          placeholder="填写说明（可选）"
          onChange={(e) => patch(f.field_name, { hint: e.target.value })}
        />
      ),
    },
    {
      title: "操作",
      key: "action",
      width: 70,
      render: (_: unknown, f: DraftField) => (
        <Tooltip title="恢复默认（按数据类型映射）">
          <Button size="small" type="link" onClick={() => resetField(f.field_name)}>
            重置
          </Button>
        </Tooltip>
      ),
    },
  ];

  return (
    <Modal
      title={`调整表单「${title}」`}
      open={open}
      onOk={submit}
      onCancel={onClose}
      okText="保存"
      cancelText="取消"
      confirmLoading={saving}
      width={1100}
      style={{ top: 24 }}
      destroyOnClose
    >
      {loading ? (
        <div style={{ textAlign: "center", padding: 48 }}>
          <Spin tip="加载表单配置…" />
        </div>
      ) : (
        <>
          {degraded && (
            <Tag color="warning" style={{ marginBottom: 8 }}>
              部分配置已失效，已按默认展示
            </Tag>
          )}
          <Space wrap style={{ marginBottom: 12 }} align="center">
            <Text strong>页面标题</Text>
            <Input
              style={{ width: 240 }}
              value={draftTitle}
              maxLength={128}
              onChange={(e) => setDraftTitle(e.target.value)}
            />
            <Text strong style={{ marginLeft: 16 }}>每行列数</Text>
            <Radio.Group value={columns} onChange={(e) => setColumns(e.target.value)}>
              <Radio.Button value={1}>单列</Radio.Button>
              <Radio.Button value={2}>双列</Radio.Button>
              <Radio.Button value={3}>三列</Radio.Button>
            </Radio.Group>
          </Space>

          <Space wrap style={{ marginBottom: 12 }} align="center">
            <Text strong>分组</Text>
            {groups.map((g, i) => (
              <Space key={i} size={2}>
                <Input
                  size="small"
                  style={{ width: 120 }}
                  value={g.title}
                  maxLength={50}
                  onChange={(e) =>
                    setGroups((prev) => prev.map((x, j) => (j === i ? { title: e.target.value } : x)))
                  }
                />
                <Button
                  size="small"
                  type="text"
                  danger
                  icon={<DeleteOutlined />}
                  onClick={() => {
                    const title = g.title;
                    setGroups((prev) => prev.filter((_, j) => j !== i));
                    setFields((prev) =>
                      prev.map((f) => (f.group_title === title ? { ...f, group_title: null } : f)),
                    );
                  }}
                />
              </Space>
            ))}
            <Button
              size="small"
              type="dashed"
              icon={<PlusOutlined />}
              onClick={() => setGroups((prev) => [...prev, { title: `分组${prev.length + 1}` }])}
            >
              新增分组
            </Button>
          </Space>

          <Row gutter={16}>
            <Col span={15}>
              <Table
                size="small"
                rowKey="field_name"
                pagination={false}
                scroll={{ y: 360 }}
                dataSource={orderedFields}
                columns={columnsDef as never}
                locale={{ emptyText: <Empty description="无可配置字段" /> }}
              />
            </Col>
            <Col span={9}>
              <Text strong style={{ display: "block", marginBottom: 8 }}>实时预览</Text>
              <div style={{ border: "1px solid var(--fn-border-primary)", borderRadius: 8, padding: 12 }}>
                <Form form={previewForm} layout="vertical">
                  <Row gutter={16}>
                    {orderedFields
                      .filter((f) => !f.hidden)
                      .map((f) => {
                        return (
                          <Col span={columns > 1 ? Math.floor(24 / columns) : 24} key={f.field_name}>
                            <Form.Item
                              name={f.field_name}
                              label={f.biz_name}
                              extra={f.hint ? <Text type="secondary">{f.hint}</Text> : undefined}
                            >
                              {renderFormControl({
                                name: f.field_name,
                                bizName: f.biz_name,
                                type: f.data_type,
                                length: f.length,
                                isFk: f.is_fk,
                                controlType: f.control_type,
                                controlConfig: f.control_config,
                                timeGranularity: f.time_granularity,
                                options: f.options,
                                disabled: f.readonly,
                                deploymentId,
                                table,
                                initialValue: undefined,
                              })}
                            </Form.Item>
                          </Col>
                        );
                      })}
                  </Row>
                </Form>
              </div>
            </Col>
          </Row>
        </>
      )}

      <Modal
        title={`候选项：${optionFieldDraft?.biz_name ?? ""}`}
        open={optionField !== null}
        onCancel={() => setOptionField(null)}
        footer={
          <Button type="primary" onClick={() => setOptionField(null)}>
            完成
          </Button>
        }
        width={560}
        destroyOnClose
      >
        {optionFieldDraft && (
          <>
            <Space.Compact style={{ width: "100%", marginBottom: 12 }}>
              <Input
                placeholder="用一句话描述候选项来源，如：常见订单状态"
                value={genText}
                onChange={(e) => setGenText(e.target.value)}
                onPressEnter={() => generateOptions(optionFieldDraft.field_name)}
              />
              <Button
                icon={<ThunderboltOutlined />}
                loading={generating}
                onClick={() => generateOptions(optionFieldDraft.field_name)}
              >
                AI 生成
              </Button>
            </Space.Compact>
            {optionFieldDraft.options.map((o, i) => (
              <Space key={i} style={{ display: "flex", marginBottom: 6 }} wrap>
                <Input
                  style={{ width: 140 }}
                  placeholder="存储值"
                  value={o.value}
                  onChange={(e) => {
                    const next = [...optionFieldDraft.options];
                    next[i] = { ...next[i], value: e.target.value };
                    patch(optionFieldDraft.field_name, { options: next });
                  }}
                />
                <Input
                  style={{ width: 150 }}
                  placeholder="展示文案"
                  value={o.label}
                  onChange={(e) => {
                    const next = [...optionFieldDraft.options];
                    next[i] = { ...next[i], label: e.target.value };
                    patch(optionFieldDraft.field_name, { options: next });
                  }}
                />
                {optionFieldDraft.control_type === "cascader" && (
                  <Select
                    style={{ width: 130 }}
                    size="small"
                    allowClear
                    placeholder="父级（顶层留空）"
                    value={o.parent_value ?? undefined}
                    options={optionFieldDraft.options
                      .filter((x) => x.value !== o.value)
                      .map((x) => ({ value: x.value, label: x.label || x.value }))}
                    onChange={(v) => {
                      const next = [...optionFieldDraft.options];
                      next[i] = { ...next[i], parent_value: v ?? null };
                      patch(optionFieldDraft.field_name, { options: next });
                    }}
                  />
                )}
                <Button
                  size="small"
                  type="text"
                  danger
                  icon={<DeleteOutlined />}
                  onClick={() =>
                    patch(optionFieldDraft.field_name, {
                      options: optionFieldDraft.options.filter((_, j) => j !== i),
                    })
                  }
                />
              </Space>
            ))}
            <Button
              type="dashed"
              icon={<PlusOutlined />}
              onClick={() =>
                patch(optionFieldDraft.field_name, {
                  options: [...optionFieldDraft.options, { value: "", label: "" }],
                })
              }
            >
              新增候选项
            </Button>
          </>
        )}
      </Modal>

      <Modal
        title={`控件参数：${paramFieldDraft?.biz_name ?? ""}`}
        open={paramField !== null}
        onCancel={() => setParamField(null)}
        onOk={() => setParamField(null)}
        okText="完成"
        cancelText="取消"
        width={440}
        destroyOnClose
      >
        {paramFieldDraft && (
          <>
            {paramFieldDraft.control_type === "slider" && (
              <Space direction="vertical" style={{ width: "100%" }}>
                <Text type="secondary">滑块范围与步长（影响录入时的可选项）</Text>
                <Space wrap>
                  <span>
                    最小
                    <InputNumber
                      style={{ width: 110, marginLeft: 8 }}
                      value={paramFieldDraft.control_config.min as number}
                      onChange={(v: number | null) =>
                        patch(paramFieldDraft.field_name, {
                          control_config: { ...paramFieldDraft.control_config, min: v ?? undefined },
                        })
                      }
                    />
                  </span>
                  <span>
                    最大
                    <InputNumber
                      style={{ width: 110, marginLeft: 8 }}
                      value={paramFieldDraft.control_config.max as number}
                      onChange={(v: number | null) =>
                        patch(paramFieldDraft.field_name, {
                          control_config: { ...paramFieldDraft.control_config, max: v ?? undefined },
                        })
                      }
                    />
                  </span>
                  <span>
                    步长
                    <InputNumber
                      style={{ width: 110, marginLeft: 8 }}
                      value={paramFieldDraft.control_config.step as number}
                      onChange={(v: number | null) =>
                        patch(paramFieldDraft.field_name, {
                          control_config: { ...paramFieldDraft.control_config, step: v ?? undefined },
                        })
                      }
                    />
                  </span>
                </Space>
              </Space>
            )}
            {paramFieldDraft.control_type === "rate" && (
              <Space direction="vertical" style={{ width: "100%" }}>
                <Space wrap>
                  <span>
                    星数（1~10）
                    <InputNumber
                      style={{ width: 110, marginLeft: 8 }}
                      min={1}
                      max={10}
                      value={paramFieldDraft.control_config.max_stars ?? 5}
                      onChange={(v: number | null) =>
                        patch(paramFieldDraft.field_name, {
                          control_config: { ...paramFieldDraft.control_config, max_stars: v ?? 5 },
                        })
                      }
                    />
                  </span>
                  <span>
                    允许半星
                    <Switch
                      style={{ marginLeft: 8 }}
                      checked={Boolean(paramFieldDraft.control_config.allow_half)}
                      onChange={(v) =>
                        patch(paramFieldDraft.field_name, {
                          control_config: { ...paramFieldDraft.control_config, allow_half: v },
                        })
                      }
                    />
                  </span>
                </Space>
                <Text type="secondary">
                  半星评分需该列为 DECIMAL 类型（保存时后端校验），整数列请关闭半星
                </Text>
              </Space>
            )}
            {paramFieldDraft.control_type === "cascader" && (
              <Space direction="vertical" style={{ width: "100%" }}>
                <span>
                  展开方式
                  <Select
                    style={{ width: 160, marginLeft: 8 }}
                    value={paramFieldDraft.control_config.expand_trigger ?? "click"}
                    options={[
                      { value: "click", label: "点击展开" },
                      { value: "hover", label: "悬停展开" },
                    ]}
                    onChange={(v) =>
                      patch(paramFieldDraft.field_name, {
                        control_config: { ...paramFieldDraft.control_config, expand_trigger: v },
                      })
                    }
                  />
                </span>
                <Text type="secondary">级联候选项请在上方「候选项」中维护，父级留空为顶层</Text>
              </Space>
            )}
          </>
        )}
      </Modal>
    </Modal>
  );
}
