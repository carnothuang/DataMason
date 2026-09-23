import { useEffect, useState } from "react";
import { Alert, Button, Modal, Spin, Typography } from "antd";
import { DownloadOutlined } from "@ant-design/icons";
import { FilesApi } from "../api";
import type { FileRef } from "./FileChip";

const { Text } = Typography;

const IMAGE_EXT = ["png", "jpg", "jpeg", "gif", "webp", "bmp"];
const TEXT_EXT = ["txt", "csv", "md", "log", "json"];
const PDF_EXT = ["pdf"];

function extOf(name?: string): string {
  if (!name) return "";
  const i = name.lastIndexOf(".");
  return i >= 0 ? name.slice(i + 1).toLowerCase() : "";
}

interface Props {
  deploymentId: number;
  file: FileRef | null;
  onClose(): void;
}

/** 附件预览：图片/PDF/文本内嵌展示，其余类型提示下载（0.13.0 补充；不新增依赖）。 */
export default function FilePreviewModal({ deploymentId, file, onClose }: Props) {
  const [text, setText] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const ext = extOf(file?.name);
  const kind = IMAGE_EXT.includes(ext)
    ? "image"
    : PDF_EXT.includes(ext)
      ? "pdf"
      : TEXT_EXT.includes(ext)
        ? "text"
        : "other";
  const previewUrl = file ? FilesApi.previewUrl(deploymentId, file.token) : "";
  const downloadUrl = file ? FilesApi.fileUrl(deploymentId, file.token) : "";

  useEffect(() => {
    if (!file) return;
    setText(null);
    setError("");
    if (kind !== "text") return;
    setLoading(true);
    let alive = true;
    fetch(previewUrl)
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(`读取失败（HTTP ${r.status}）`))))
      .then((t) => alive && setText(t))
      .catch((e) => alive && setError((e as Error).message))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [file, kind, previewUrl]);

  const body = () => {
    if (!file) return null;
    if (kind === "image") {
      return (
        <div style={{ textAlign: "center" }}>
          <img
            src={previewUrl}
            alt={file.name || file.token}
            style={{ maxWidth: "100%", maxHeight: "70vh" }}
            onError={() => setError("图片无法加载：文件可能已丢失或未随数据一起迁移")}
          />
          {error && <Alert type="warning" showIcon message={error} style={{ marginTop: 12 }} />}
        </div>
      );
    }
    if (kind === "pdf") {
      return (
        <iframe
          title={file.name || file.token}
          src={previewUrl}
          style={{ width: "100%", height: "70vh", border: 0 }}
        />
      );
    }
    if (kind === "text") {
      if (loading) return <div style={{ textAlign: "center", padding: 40 }}><Spin /></div>;
      if (error) return <Alert type="warning" showIcon message={error} />;
      return (
        <pre style={{ maxHeight: "70vh", overflow: "auto", background: "#fafafa", padding: 12, margin: 0 }}>
          {text}
        </pre>
      );
    }
    return (
      <Alert
        type="info"
        showIcon
        message="该类型暂不支持在线预览"
        description={
          <span>
            请下载后使用本地软件查看：<Text code>{file.name || file.token}</Text>
          </span>
        }
      />
    );
  };

  return (
    <Modal
      open={file !== null}
      onCancel={onClose}
      width={880}
      title={file?.name || "附件预览"}
      destroyOnClose
      footer={
        <Button
          icon={<DownloadOutlined />}
          href={downloadUrl || undefined}
          target="_blank"
          rel="noreferrer"
        >
          下载
        </Button>
      }
    >
      {body()}
    </Modal>
  );
}
