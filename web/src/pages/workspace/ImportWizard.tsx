import { useState } from "react";
import {
  Alert,
  App,
  Button,
  Modal,
  Radio,
  Select,
  Space,
  Steps,
  Table,
  Tag,
  Typography,
  Upload,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { UploadOutlined } from "@ant-design/icons";
import { ImportApi, ImportPreview } from "../../api";

const { Text } = Typography;

interface Props {
  deploymentId: number;
  table: string;
  tableBizName: string;
  open: boolean;
  onClose: () => void;
  onImported: () => void;
}

interface MapRow {
  name: string;
  biz_name: string;
  type: string;
  nullable: boolean;
  is_pk: boolean;
  is_ownership: boolean;
  headerIndex?: number;
}

/** 0.11.0 M5-B4：导入四步向导（上传 → 列映射 → 校验预览 → 确认导入）。 */
export default function ImportWizard({
  deploymentId, table, tableBizName, open, onClose, onImported,
}: Props) {
  const { message } = App.useApp();
  const [step, setStep] = useState(0);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [rows, setRows] = useState<MapRow[]>([]);
  const [mode, setMode] = useState<"strict" | "skip_errors">("strict");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ inserted: number; skipped: number } | null>(null);

  const reset = () => {
    setStep(0); setFile(null); setPreview(null); setRows([]);
    setMode("strict"); setResult(null);
  };

  const doPreview = async (f: File) => {
    setBusy(true);
    try {
      const p = await ImportApi.preview(deploymentId, table, f);
      setPreview(p);
      const mapRows: MapRow[] = p.columns.map((c) => ({
        name: c.name, biz_name: c.biz_name, type: c.type,
        nullable: c.nullable, is_pk: c.is_pk, is_ownership: c.is_ownership,
        headerIndex: p.mapping[c.name],
      }));
      setRows(mapRows);
      setStep(1);
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const doCommit = async () => {
    if (!preview) return;
    setBusy(true);
    try {
      const mapping: Record<string, number> = {};
      for (const r of rows) if (r.headerIndex !== undefined) mapping[r.name] = r.headerIndex;
      const r = await ImportApi.commit(deploymentId, table, preview.upload_token, mapping, mode);
      setResult(r);
      setStep(3);
      onImported();
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const mapCols: ColumnsType<MapRow> = [
    { title: "字段", dataIndex: "biz_name", render: (v, r) => (
      <Space size={4}><Text>{v}</Text><Text type="secondary" style={{ fontSize: 12 }}>{r.name}</Text>
        {r.is_ownership && <Tag>系统</Tag>}</Space>
    ) },
    { title: "类型", dataIndex: "type", width: 110 },
    { title: "必填", dataIndex: "nullable", width: 60, render: (v) => (v ? "否" : "是") },
    { title: "对应文件列", key: "map", width: 240, render: (_v, r, i) => (
      <Select
        allowClear
        size="small"
        style={{ width: "100%" }}
        placeholder="忽略此字段"
        value={r.headerIndex}
        options={(preview?.headers || []).map((h, idx) => ({ value: idx, label: `${h || `第${idx + 1}列`}` }))}
        onChange={(val) => setRows((prev) => prev.map((x, idx) => idx === i ? { ...x, headerIndex: val } : x))}
      />
    ) },
  ];

  return (
    <Modal
      title={`导入数据 · ${tableBizName}`}
      open={open}
      onCancel={() => { reset(); onClose(); }}
      width={880}
      footer={null}
      destroyOnClose
    >
      <Steps
        size="small"
        current={step}
        items={[{ title: "上传文件" }, { title: "列映射" }, { title: "校验预览" }, { title: "完成" }]}
        style={{ marginBottom: 16 }}
      />

      {step === 0 && (
        <Space direction="vertical">
          <Text type="secondary">支持 CSV / XLSX；系统会自动按字段名匹配列，下一步可调整。</Text>
          <Upload
            accept=".csv,.xlsx,.xlsm"
            maxCount={1}
            showUploadList={false}
            beforeUpload={(f) => { setFile(f as File); doPreview(f as File); return false; }}
          >
            <Button icon={<UploadOutlined />} loading={busy}>选择文件</Button>
          </Upload>
          {file && <Text>{file.name}</Text>}
          <a href={ImportApi.templateUrl(deploymentId, table)} target="_blank" rel="noreferrer">
            下载导入模板
          </a>
        </Space>
      )}

      {step === 1 && preview && (
        <Space direction="vertical" style={{ width: "100%" }}>
          <Table<MapRow>
            rowKey="name"
            size="small"
            pagination={false}
            dataSource={rows}
            columns={mapCols}
          />
          <Space>
            <Button onClick={() => setStep(0)}>上一步</Button>
            <Button type="primary" onClick={() => setStep(2)}>校验预览</Button>
          </Space>
        </Space>
      )}

      {step === 2 && preview && (
        <Space direction="vertical" style={{ width: "100%" }}>
          <Alert
            type={preview.error_count ? "warning" : "success"}
            showIcon
            message={`共 ${preview.total} 行：可导入 ${preview.valid} 行，问题 ${preview.error_count} 行`}
          />
          {preview.error_count > 0 && (
            <Table
              rowKey={(r) => `${r.row}-${r.column}-${r.message}`}
              size="small"
              pagination={{ pageSize: 8 }}
              dataSource={preview.errors}
              columns={[
                { title: "行号", dataIndex: "row", width: 80 },
                { title: "字段", dataIndex: "column", width: 160 },
                { title: "问题", dataIndex: "message" },
              ]}
            />
          )}
          <Radio.Group value={mode} onChange={(e) => setMode(e.target.value)}>
            <Radio value="strict">全量导入（有错误则整体不导入）</Radio>
            <Radio value="skip_errors">跳过错误行，只导入正确行</Radio>
          </Radio.Group>
          <Space>
            <Button onClick={() => setStep(1)}>上一步</Button>
            <Button type="primary" loading={busy} onClick={doCommit}>确认导入</Button>
          </Space>
        </Space>
      )}

      {step === 3 && result && (
        <Space direction="vertical">
          <Alert type="success" showIcon
                 message={`导入完成：成功 ${result.inserted} 行，跳过 ${result.skipped} 行`} />
          <Button type="primary" onClick={() => { reset(); onClose(); }}>关闭</Button>
        </Space>
      )}
    </Modal>
  );
}
