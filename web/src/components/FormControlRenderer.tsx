import { useCallback, useEffect, useRef, useState } from "react";
import { App, Button, Checkbox, DatePicker, Input, InputNumber, Radio, Select, Space, Upload } from "antd";
import { EyeOutlined, UploadOutlined } from "@ant-design/icons";
import dayjs, { Dayjs } from "dayjs";
import type { MetaColumn, RefOption } from "../api";
import { CrudApi, FilesApi } from "../api";
import { parseFileValue } from "./FileChip";
import FilePreviewModal from "./FilePreviewModal";

/** 字段类型基名（去长度参数、大写）。 */
export function baseType(type: string): string {
  return (type || "").split("(")[0].trim().toUpperCase();
}

/** 控件类型中文名（配置器与提示共用）。 */
export const CONTROL_LABELS: Record<string, string> = {
  text: "单行文本",
  textarea: "多行文本",
  number: "数字",
  boolean: "是否",
  radio: "单选",
  checkbox: "多选",
  date: "日期",
  datetime: "日期时间",
  ref: "关联选择",
  file: "附件",
};

const STRING_TYPES = ["VARCHAR", "TEXT", "CHAR", "STRING", "ENUM"];
const NUMERIC_TYPES = ["INTEGER", "BIGINT", "DECIMAL"];

/** 字段类型 → 相容控件类型（与后端 spec.CONTROL_COMPAT 对齐）。 */
export function compatibleControls(dataType: string, isFk: boolean): string[] {
  const t = baseType(dataType);
  const out: string[] = [];
  if (isFk) out.push("ref");
  if (t === "FILE") return ["file"];
  if (t === "BOOLEAN") return [...out, "boolean", "radio", "checkbox"];
  if (STRING_TYPES.includes(t)) out.push("text", "textarea", "radio", "checkbox");
  else if (NUMERIC_TYPES.includes(t)) out.push("number", "radio", "checkbox");
  else if (t === "DATETIME" || t === "DATE" || t === "TIMESTAMP") out.push("datetime", "date");
  return out;
}

/** 零配置时的默认控件映射（与升级前表单渲染一致）。 */
export function defaultControlType(type: string, isFk: boolean): string {
  if (isFk) return "ref";
  const t = baseType(type);
  if (t === "FILE") return "file";
  if (t === "BOOLEAN") return "boolean";
  if (NUMERIC_TYPES.includes(t)) return "number";
  if (t === "DATETIME" || t === "TIMESTAMP" || t === "DATE") return "datetime";
  if (t === "TEXT") return "textarea";
  return "text";
}

/** 外键关联下拉：远程 refs 搜索，逐条加载 option（行为与 M2-4 一致）。 */
export function FkSelect({
  id,
  value,
  onChange,
  deploymentId,
  table,
  fieldName,
  bizName,
  initialValue,
  disabled,
}: {
  id?: string;
  value?: string | number | null;
  onChange?: (v: string | number | null) => void;
  deploymentId: number;
  table: string;
  fieldName: string;
  bizName: string;
  initialValue: unknown;
  disabled?: boolean;
}) {
  const { message } = App.useApp();
  const [options, setOptions] = useState<RefOption[]>([]);
  const [loading, setLoading] = useState(false);
  const debounceRef = useRef<number | undefined>(undefined);

  const load = useCallback(
    async (search = "") => {
      setLoading(true);
      try {
        const r = await CrudApi.refs(deploymentId, table, fieldName, { search, page_size: 50 });
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
    [deploymentId, table, fieldName, initialValue, message],
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
      disabled={disabled}
      filterOption={false}
      loading={loading}
      options={options}
      placeholder={`请选择${bizName}`}
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

const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;

/** FILE 字段控件（0.11.0 M5-B2）：上传/多附件；值存 [{token,name}] 的 JSON 串。
 *  M7-5-3：移除仅解除字段值引用（不删附件记录/磁盘文件，避免误删被其他记录引用的附件）。 */
export function FileField({
  deploymentId,
  value,
  onChange,
  disabled,
}: {
  deploymentId: number;
  value?: unknown;
  onChange?: (v: unknown) => void;
  disabled?: boolean;
}) {
  const { message } = App.useApp();
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<{ token: string; name?: string } | null>(null);
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

  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <Upload
        maxCount={1}
        showUploadList={false}
        disabled={disabled}
        beforeUpload={(f) => {
          const file = f as File;
          if (file.size > MAX_UPLOAD_BYTES) {
            message.error("文件超过 10 MB 上限，请压缩后再上传");
            return Upload.LIST_IGNORE;
          }
          upload(file);
          return false;
        }}
      >
        <Button icon={<UploadOutlined />} loading={busy} disabled={disabled}>
          上传附件
        </Button>
      </Upload>
      {files.map((f) => (
        <Space key={f.token} size={8}>
          <a href={FilesApi.fileUrl(deploymentId, f.token)} target="_blank" rel="noreferrer">
            {f.name || f.token.slice(0, 8)}
          </a>
          <Button
            size="small"
            type="link"
            icon={<EyeOutlined />}
            onClick={() => setPreview({ token: f.token, name: f.name })}
          >
            预览
          </Button>
          <Button
            size="small"
            type="link"
            danger
            disabled={disabled}
            onClick={() => push(files.filter((x) => x.token !== f.token))}
          >
            移除
          </Button>
        </Space>
      ))}
      {preview && (
        <FilePreviewModal
          deploymentId={deploymentId}
          file={preview}
          onClose={() => setPreview(null)}
        />
      )}
    </Space>
  );
}

/** 多选值兼容：DB 存 JSON 串，Checkbox.Group 需数组。 */
function CheckboxField({
  value,
  onChange,
  options,
  disabled,
}: {
  value?: unknown;
  onChange?: (v: unknown) => void;
  options: { value: string; label: string }[];
  disabled?: boolean;
}) {
  let arr: string[] = [];
  if (Array.isArray(value)) arr = value.map(String);
  else if (typeof value === "string" && value.trim()) {
    try {
      const parsed = JSON.parse(value);
      arr = Array.isArray(parsed) ? parsed.map(String) : [];
    } catch {
      arr = [];
    }
  }
  return (
    <Checkbox.Group
      disabled={disabled}
      options={options}
      value={arr}
      onChange={(v) => onChange?.(v)}
    />
  );
}

/** 日期/日期时间：字符串值 ↔ dayjs。 */
function DateField({
  value,
  onChange,
  showTime,
  disabled,
}: {
  value?: unknown;
  onChange?: (v: unknown) => void;
  showTime: boolean;
  disabled?: boolean;
}) {
  const fmt = showTime ? "YYYY-MM-DD HH:mm:ss" : "YYYY-MM-DD";
  const d = typeof value === "string" && value ? dayjs(value) : null;
  return (
    <DatePicker
      style={{ width: "100%" }}
      showTime={showTime}
      disabled={disabled}
      value={d && d.isValid() ? d : null}
      format={fmt}
      placeholder={fmt}
      onChange={(v: Dayjs | null) => onChange?.(v ? v.format(fmt) : "")}
    />
  );
}

export interface RenderControlOptions {
  name: string;
  bizName: string;
  type: string;
  length: number | null;
  isFk: boolean;
  controlType?: string | null;
  timeGranularity?: "date" | "datetime" | null;
  options?: { value: string; label: string }[];
  disabled?: boolean;
  deploymentId: number;
  table: string;
  initialValue?: unknown;
}

/** 按字段配置渲染单个控件；controlType 为空时走默认映射（零配置零变化）。 */
export function renderFormControl(o: RenderControlOptions) {
  const control = o.controlType || defaultControlType(o.type, o.isFk);
  const bt = baseType(o.type);
  const opts = o.options || [];
  const placeholder = `请输入${o.bizName}`;

  if (control === "file" || bt === "FILE") {
    return <FileField deploymentId={o.deploymentId} disabled={o.disabled} />;
  }
  if (control === "ref" || o.isFk) {
    return (
      <FkSelect
        deploymentId={o.deploymentId}
        table={o.table}
        fieldName={o.name}
        bizName={o.bizName}
        initialValue={o.initialValue}
        disabled={o.disabled}
      />
    );
  }
  if (control === "radio") {
    if (!opts.length) return <Input disabled={o.disabled} placeholder="尚未配置候选项" />;
    return (
      <Radio.Group disabled={o.disabled}>
        {opts.map((it) => (
          <Radio key={it.value} value={it.value}>
            {it.label}
          </Radio>
        ))}
      </Radio.Group>
    );
  }
  if (control === "checkbox") {
    if (!opts.length) return <Input disabled={o.disabled} placeholder="尚未配置候选项" />;
    return <CheckboxField options={opts} disabled={o.disabled} />;
  }
  if (control === "date" || control === "datetime") {
    const showTime = control === "datetime";
    return <DateField showTime={showTime} disabled={o.disabled} />;
  }
  if (control === "boolean" || bt === "BOOLEAN") {
    return (
      <Select
        allowClear
        disabled={o.disabled}
        options={[
          { value: 1, label: "是" },
          { value: 0, label: "否" },
        ]}
        placeholder={`请选择${o.bizName}`}
      />
    );
  }
  if (control === "number") {
    const precision = bt === "DECIMAL" || bt === "NUMERIC" ? 2 : 0;
    return (
      <InputNumber
        style={{ width: "100%" }}
        precision={precision}
        disabled={o.disabled}
        placeholder={placeholder}
      />
    );
  }
  if (control === "textarea") {
    return <Input.TextArea rows={3} disabled={o.disabled} placeholder={placeholder} />;
  }
  return <Input disabled={o.disabled} placeholder={placeholder} />;
}

/** 供配置器预览：把 MetaColumn 转成 RenderControlOptions（不含值，由预览表单注入）。 */
export function renderColumnPreview(
  col: MetaColumn,
  deploymentId: number,
  table: string,
  controlType: string | null,
  options: { value: string; label: string }[],
  timeGranularity: "date" | "datetime" | null,
) {
  return renderFormControl({
    name: col.name,
    bizName: col.biz_name || col.name,
    type: col.type,
    length: col.length,
    isFk: !!col.fk,
    controlType,
    options,
    timeGranularity,
    deploymentId,
    table,
  });
}
