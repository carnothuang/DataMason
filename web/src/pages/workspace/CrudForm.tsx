import { useCallback, useEffect, useMemo, useState } from "react";
import { App, Button, Col, Drawer, Form, Row, Space, Typography } from "antd";
import type { CrudRowFilters, FormConfigResponse, FormFieldConfig, MetaColumn } from "../../api";
import { CrudApi, FormsApi } from "../../api";
import { baseType, renderFormControl } from "../../components/FormControlRenderer";

export type CrudFormRecord = Record<string, unknown>;
export { baseType };

const { Text } = Typography;

interface Props {
  deploymentId: number;
  table: string;
  meta: MetaColumn[];
  pkName: string;
  initial: CrudFormRecord | null;
  open: boolean;
  onClose(): void;
  onSaved(): void;
}

/** M2-4 CRUD 表单；0.13.0 M7-5 改为界面配置驱动（control_type 为空走默认映射，零配置零变化）。 */
export default function CrudForm({
  deploymentId,
  table,
  meta,
  pkName,
  initial,
  open,
  onClose,
  onSaved,
}: Props) {
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);
  const [config, setConfig] = useState<FormConfigResponse | null>(null);

  const editable = useMemo(
    () => meta.filter((c) => !c.is_ownership && !c.pk),
    [meta],
  );

  // 打开时拉运行态配置；失败则降级为默认映射（不阻断录入）。
  useEffect(() => {
    if (!open) return;
    let alive = true;
    FormsApi.runtimeConfig(deploymentId, table)
      .then((c) => alive && setConfig(c))
      .catch(() => alive && setConfig(null));
    return () => {
      alive = false;
    };
  }, [open, deploymentId, table]);

  useEffect(() => {
    if (!open) return;
    if (initial) form.setFieldsValue(initial);
    else form.resetFields();
  }, [open, initial, form]);

  const cfgByName = useMemo(() => {
    const m = new Map<string, FormFieldConfig>();
    for (const f of config?.fields || []) m.set(f.field_name, f);
    return m;
  }, [config]);

  const visible = useMemo(
    () => editable.filter((c) => !cfgByName.get(c.name)?.hidden),
    [editable, cfgByName],
  );

  const ordered = useMemo(() => {
    const order = config?.layout?.order || [];
    const inOrder = order
      .map((n) => visible.find((c) => c.name === n))
      .filter((c): c is MetaColumn => !!c);
    const rest = visible.filter((c) => !order.includes(c.name));
    return [...inOrder, ...rest];
  }, [visible, config]);

  const columns = config?.layout?.columns_per_row || 1;
  const span = columns > 1 ? Math.floor(24 / columns) : 24;

  const checkUnique = (col: MetaColumn) => async (_rule: unknown, value: unknown) => {
    if (value === undefined || value === null || String(value).trim() === "") return;
    const params: CrudRowFilters = { [col.name]: String(value).trim(), page: 1, page_size: 10 };
    const res = await CrudApi.rows(deploymentId, table, params);
    const clash = res.data.some(
      (r) => initial === null || String(r[pkName]) !== String(initial[pkName]),
    );
    if (clash) throw new Error(`「${col.biz_name || col.name}」已存在相同取值`);
  };

  const buildPayload = useCallback(
    (values: Record<string, unknown>): Record<string, unknown> => {
      const payload: Record<string, unknown> = {};
      for (const col of visible) {
        const v = values[col.name];
        if (v === undefined || v === null || v === "") continue;
        payload[col.name] = v;
      }
      return payload;
    },
    [visible],
  );

  const submit = async () => {
    try {
      const values = await form.validateFields();
      setSaving(true);
      const payload = buildPayload(values);
      if (initial) {
        await CrudApi.update(deploymentId, table, initial[pkName] as string | number, payload);
      } else {
        await CrudApi.create(deploymentId, table, payload);
      }
      message.success(initial ? "已保存" : "已新增");
      onSaved();
      onClose();
    } catch (e) {
      if (e && typeof e === "object" && "errorFields" in (e as object)) return;
      message.error((e as Error).message || "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const rulesFor = (col: MetaColumn) => {
    const cf = cfgByName.get(col.name);
    const rules: Record<string, unknown>[] = [];
    if (!col.nullable) {
      rules.push({ required: true, message: `请填写${col.biz_name || col.name}` });
    }
    if (baseType(col.type) === "VARCHAR" && col.length) {
      rules.push({ max: col.length, message: `长度不能超过 ${col.length} 个字符` });
    }
    if (col.unique) rules.push({ validator: checkUnique(col) });
    if (cf?.control_type === "checkbox" && col.length) {
      rules.push({
        validator: (_r: unknown, v: unknown) => {
          let arr: unknown[] = [];
          if (Array.isArray(v)) arr = v;
          else if (typeof v === "string" && v) {
            try {
              const p = JSON.parse(v);
              arr = Array.isArray(p) ? p : [];
            } catch {
              arr = [];
            }
          }
          if (arr.length && JSON.stringify(arr).length > col.length!) {
            return Promise.reject(new Error(`已选内容过长，请减少选择项（上限 ${col.length} 字符）`));
          }
          return Promise.resolve();
        },
      });
    }
    return rules;
  };

  const renderItem = (col: MetaColumn) => {
    const cf = cfgByName.get(col.name);
    const label = col.biz_name || col.name;
    return (
      <Col span={span} key={col.name}>
        <Form.Item
          name={col.name}
          label={label}
          rules={rulesFor(col)}
          extra={cf?.hint ? <Text type="secondary">{cf.hint}</Text> : undefined}
        >
          {renderFormControl({
            name: col.name,
            bizName: label,
            type: col.type,
            length: col.length,
            isFk: !!col.fk,
            controlType: cf?.control_type ?? null,
            timeGranularity: cf?.time_granularity ?? null,
            options: cf?.options,
            disabled: cf?.readonly,
            deploymentId,
            table,
            initialValue: initial?.[col.name],
          })}
        </Form.Item>
      </Col>
    );
  };

  const groups = config?.layout?.groups || [];
  const grouped = new Set<string>();
  for (const g of groups) for (const n of g.field_names) grouped.add(n);
  const ungrouped = ordered.filter((c) => !grouped.has(c.name));

  return (
    <Drawer
      title={initial ? `编辑${table}记录` : `新增${table}记录`}
      open={open}
      onClose={onClose}
      width={columns > 1 ? 720 : 480}
      extra={
        <Space>
          <Button onClick={onClose}>取消</Button>
          <Button type="primary" loading={saving} onClick={submit}>
            {initial ? "保存" : "新增"}
          </Button>
        </Space>
      }
    >
      <Form form={form} layout="vertical">
        {groups.map((g, gi) => {
          const items = ordered.filter((c) => g.field_names.includes(c.name));
          if (!items.length) return null;
          return (
            <div key={`g-${gi}`}>
              <Text strong>{g.title}</Text>
              <Row gutter={16} style={{ marginTop: 8 }}>{items.map(renderItem)}</Row>
            </div>
          );
        })}
        <Row gutter={16}>{ungrouped.map(renderItem)}</Row>
      </Form>
    </Drawer>
  );
}
