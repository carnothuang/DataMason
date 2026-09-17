import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { App, Button, Drawer, Form, Input, InputNumber, Select, Space, Upload } from "antd";
import { UploadOutlined } from "@ant-design/icons";
import type { CrudRowFilters, MetaColumn, RefOption } from "../../api";
import { CrudApi, FilesApi } from "../../api";
import { parseFileValue } from "../../components/FileChip";

export type CrudFormRecord = Record<string, unknown>;

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

export function baseType(type: string): string {
  return type.split("(")[0].trim().toUpperCase();
}

/** 外键关联下拉：远程 refs 搜索，逐条加载 option。 */
function FkSelect({
  id,
  value,
  onChange,
  deploymentId,
  table,
  col,
  initialValue,
}: {
  id?: string;
  value?: string | number | null;
  onChange?: (v: string | number | null) => void;
  deploymentId: number;
  table: string;
  col: MetaColumn;
  initialValue: unknown;
}) {
  const { message } = App.useApp();
  const [options, setOptions] = useState<RefOption[]>([]);
  const [loading, setLoading] = useState(false);
  const debounceRef = useRef<number | undefined>(undefined);

  const load = useCallback(
    async (search = "") => {
      setLoading(true);
      try {
        const r = await CrudApi.refs(deploymentId, table, col.name, {
          search,
          page_size: 50,
        });
        const merged =
          initialValue === undefined ||
          initialValue === null ||
          r.options.some((o) => String(o.value) === String(initialValue))
            ? r.options
            : [{ value: initialValue as string | number, label: String(initialValue) }, ...r.options];
        setOptions(merged);
      } catch (e) {
        message.error((e as Error).message);
      } finally {
        setLoading(false);
      }
    },
    [deploymentId, table, col.name, initialValue],
  );

  useEffect(() => {
    load();
  }, [load]);

  return (
    <Select
      id={id}
      value={value}
      onChange={onChange}
      allowClear
      showSearch
      filterOption={false}
      loading={loading}
      options={options}
      placeholder={`请选择${col.biz_name || col.name}`}
      onSearch={(v) => {
        if (debounceRef.current) window.clearTimeout(debounceRef.current);
        debounceRef.current = window.setTimeout(() => load(v), 300);
      }}
      onDropdownVisibleChange={(open) => {
        if (open) load();
      }}
    />
  );
}

/** M2-4 CRUD 表单：控件按列类型映射，必填/长度/唯一校验，外键远程下拉。 */
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

  const editable = useMemo(
    () => meta.filter((c) => !c.is_ownership && !c.pk),
    [meta],
  );

  useEffect(() => {
    if (!open) return;
    if (initial) form.setFieldsValue(initial);
    else form.resetFields();
  }, [open, initial, form]);

  const checkUnique = (col: MetaColumn) => async (_rule: unknown, value: unknown) => {
    if (value === undefined || value === null || String(value).trim() === "") return;
    const params: CrudRowFilters = {
      [col.name]: String(value).trim(),
      page: 1,
      page_size: 10,
    };
    const res = await CrudApi.rows(deploymentId, table, params);
    const clash = res.data.some(
      (r) => initial === null || String(r[pkName]) !== String(initial[pkName]),
    );
    if (clash) throw new Error(`「${col.biz_name || col.name}」已存在相同取值`);
  };

  const buildPayload = (values: Record<string, unknown>): Record<string, unknown> => {
    const payload: Record<string, unknown> = {};
    for (const col of editable) {
      const v = values[col.name];
      if (v === undefined || v === null || v === "") continue;
      payload[col.name] = v;
    }
    return payload;
  };

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

  const renderWidget = (col: MetaColumn) => {
    const bt = baseType(col.type);
    if (col.type === "FILE") {
      return <FileField deploymentId={deploymentId} />;
    }
    if (col.fk) {
      return <FkSelect deploymentId={deploymentId} table={table} col={col} initialValue={initial?.[col.name]} />;
    }
    if (bt === "BOOLEAN") {
      return (
        <Select
          allowClear
          options={[
            { value: 1, label: "是" },
            { value: 0, label: "否" },
          ]}
          placeholder={`请选择${col.biz_name || col.name}`}
        />
      );
    }
    if (bt === "INTEGER" || bt === "BIGINT") {
      return <InputNumber style={{ width: "100%" }} precision={0} placeholder={`请输入${col.biz_name || col.name}`} />;
    }
    if (bt === "DECIMAL" || bt === "NUMERIC") {
      return <InputNumber style={{ width: "100%" }} precision={2} placeholder={`请输入${col.biz_name || col.name}`} />;
    }
    if (bt === "TEXT") {
      return <Input.TextArea rows={3} placeholder={`请输入${col.biz_name || col.name}`} />;
    }
    return <Input placeholder={bt === "DATETIME" ? "YYYY-MM-DD HH:MM:SS" : `请输入${col.biz_name || col.name}`} />;
  };

  return (
    <Drawer
      title={initial ? `编辑${table}记录` : `新增${table}记录`}
      open={open}
      onClose={onClose}
      width={480}
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
        {editable.map((col) => {
          const rules = [
            ...(col.nullable
              ? []
              : [{ required: true, message: `请填写${col.biz_name || col.name}` }]),
            ...(baseType(col.type) === "VARCHAR" && col.length
              ? [{ max: col.length, message: `长度不能超过 ${col.length} 个字符` }]
              : []),
            ...(col.unique ? [{ validator: checkUnique(col) }] : []),
          ];
          return (
            <Form.Item
              key={col.name}
              name={col.name}
              label={col.biz_name || col.name}
              rules={rules}
            >
              {renderWidget(col)}
            </Form.Item>
          );
        })}
      </Form>
    </Drawer>
  );
}

/** FILE 字段控件（0.11.0 M5-B2）：上传/多附件；值存 [{token,name}] 的 JSON 串。 */
function FileField({
  deploymentId,
  value,
  onChange,
}: {
  deploymentId: number;
  value?: unknown;
  onChange?: (v: unknown) => void;
}) {
  const { message } = App.useApp();
  const [busy, setBusy] = useState(false);
  const files = parseFileValue(value);

  const push = (list: { token: string; name?: string }[]) =>
    onChange?.(list.length ? JSON.stringify(list) : "");

  const upload = async (f: File) => {
    setBusy(true);
    try {
      const info = await FilesApi.upload(deploymentId, f);
      push([...files, { token: info.token, name: info.filename }]);
      message.success("已上传");
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const remove = async (token: string) => {
    try {
      await FilesApi.remove(deploymentId, token);
    } catch {
      /* 文件可能已不存在，忽略 */
    }
    push(files.filter((x) => x.token !== token));
  };

  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <Upload
        maxCount={1}
        showUploadList={false}
        beforeUpload={(f) => { upload(f as File); return false; }}
      >
        <Button icon={<UploadOutlined />} loading={busy}>上传附件</Button>
      </Upload>
      {files.map((f) => (
        <Space key={f.token} size={8}>
          <a href={FilesApi.fileUrl(deploymentId, f.token)} target="_blank" rel="noreferrer">
            {f.name || f.token.slice(0, 8)}
          </a>
          <Button size="small" type="link" danger onClick={() => remove(f.token)}>移除</Button>
        </Space>
      ))}
    </Space>
  );
}