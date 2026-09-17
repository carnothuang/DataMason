import { Space, Tag, Typography } from "antd";
import { FileOutlined } from "@ant-design/icons";
import { FilesApi } from "../api";

const { Text } = Typography;

export interface FileRef {
  token: string;
  name?: string;
}

/** 解析 FILE 列的值：支持单 token 字符串 / {token,name} / 二者数组。 */
export function parseFileValue(raw: unknown): FileRef[] {
  if (raw === null || raw === undefined || raw === "") return [];
  let v: unknown = raw;
  if (typeof raw === "string") {
    const s = raw.trim();
    if (!s) return [];
    if (s.startsWith("{") || s.startsWith("[")) {
      try {
        v = JSON.parse(s);
      } catch {
        v = s;
      }
    } else {
      return [{ token: s }];
    }
  }
  if (typeof v === "string") return [{ token: v }];
  if (Array.isArray(v)) return v.flatMap(parseFileValue);
  if (typeof v === "object" && v !== null && "token" in (v as Record<string, unknown>)) {
    const o = v as Record<string, unknown>;
    return [{ token: String(o.token), name: o.name ? String(o.name) : undefined }];
  }
  return [];
}

interface Props {
  deploymentId: number;
  value: unknown;
  /** 演示脱敏：只显示占位，不触发下载 */
  masked?: boolean;
}

/** 附件字段的只读展示：文件名 + 预览/下载。 */
export default function FileChip({ deploymentId, value, masked }: Props) {
  const files = parseFileValue(value);
  if (files.length === 0) return <Text type="secondary">—</Text>;
  if (masked) return <Tag>附件 · 已脱敏</Tag>;
  return (
    <Space size={4} wrap>
      {files.map((f) => (
        <Space key={f.token} size={2}>
          <FileOutlined />
          <a href={FilesApi.fileUrl(deploymentId, f.token)} target="_blank" rel="noreferrer">
            {f.name || f.token.slice(0, 8)}
          </a>
          <a
            href={FilesApi.previewUrl(deploymentId, f.token)}
            target="_blank"
            rel="noreferrer"
            style={{ fontSize: 12 }}
          >
            预览
          </a>
        </Space>
      ))}
    </Space>
  );
}
