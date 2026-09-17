import { useState } from "react";
import {
  Alert,
  App,
  Button,
  Checkbox,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { DeployApi, NewTableColumnSpec, NewTableSpec } from "../../api";

const { Text } = Typography;

const TYPE_OPTIONS = [
  "INTEGER", "BIGINT", "VARCHAR", "TEXT", "BOOLEAN", "DATETIME", "DECIMAL", "FILE",
].map((v) => ({ value: v, label: v }));

interface Draft {
  name: string;
  biz_name: string;
  type: string;
  length: number | null;
  nullable: boolean;
  is_pk: boolean;
  autoincrement: boolean;
  unique: boolean;
  is_fk: boolean;
  ref_table: string;
}

function emptyRow(): Draft {
  return {
    name: "", biz_name: "", type: "VARCHAR", length: 255, nullable: true,
    is_pk: false, autoincrement: false, unique: false, is_fk: false, ref_table: "",
  };
}

interface Props {
  deploymentId: number;
  open: boolean;
  existingTables: string[];
  onClose: () => void;
  onCreated: () => void;
}

/** 0.11.0 M5-A：新建表（表单式 + 一句话辅助）→ 提案卡 → 确认建表。 */
export default function NewTableModal({
  deploymentId, open, existingTables, onClose, onCreated,
}: Props) {
  const { message } = App.useApp();
  const [tableName, setTableName] = useState("");
  const [bizName, setBizName] = useState("");
  const [rows, setRows] = useState<Draft[]>([
    { ...emptyRow(), name: "id", type: "INTEGER", is_pk: true, autoincrement: true, nullable: false },
  ]);
  const [nlDesc, setNlDesc] = useState("");
  const [busy, setBusy] = useState(false);
  const [proposal, setProposal] = useState<Awaited<ReturnType<typeof DeployApi.proposeAddTable>> | null>(null);

  const reset = () => {
    setTableName(""); setBizName(""); setNlDesc(""); setProposal(null);
    setRows([{ ...emptyRow(), name: "id", type: "INTEGER", is_pk: true, autoincrement: true, nullable: false }]);
  };

  const patch = (i: number, p: Partial<Draft>) =>
    setRows((prev) => prev.map((r, idx) => (idx === i ? { ...r, ...p } : r)));

  const buildSpec = (): NewTableSpec => ({
    name: tableName.trim(),
    biz_name: bizName.trim() || tableName.trim(),
    columns: rows
      .filter((r) => r.name.trim())
      .map<NewTableColumnSpec>((r) => ({
        name: r.name.trim(),
        biz_name: r.biz_name.trim(),
        type: r.type,
        length: r.type === "VARCHAR" ? (r.length || 255) : null,
        nullable: r.is_pk ? false : r.nullable,
        is_pk: r.is_pk,
        autoincrement: r.autoincrement,
        unique: r.unique,
        is_fk: r.is_fk,
        ref_table: r.is_fk ? r.ref_table : null,
        ref_column: r.is_fk ? "id" : null,
      })),
  });

  const onNlGenerate = async () => {
    if (!nlDesc.trim()) return;
    setBusy(true);
    try {
      const p = await DeployApi.preview(nlDesc.trim());
      const t = p.tables?.[0];
      if (!t) {
        message.warning("未能从描述中生成表");
        return;
      }
      setTableName(t.name);
      setBizName(t.biz_name || t.name);
      setRows(
        t.columns.map((c) => ({
          name: c.name,
          biz_name: c.biz_name || "",
          type: (c.type || "VARCHAR").toUpperCase(),
          length: 255,
          nullable: !c.is_pk,
          is_pk: !!c.is_pk,
          autoincrement: !!c.is_pk,
          unique: false,
          is_fk: !!c.is_fk,
          ref_table: c.ref_table || "",
        })),
      );
      message.success(`已生成「${t.name}」，可继续调整后生成提案`);
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const onPropose = async () => {
    if (!tableName.trim()) {
      message.error("请填写表名");
      return;
    }
    setBusy(true);
    try {
      setProposal(await DeployApi.proposeAddTable(deploymentId, buildSpec()));
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const onApply = async () => {
    setBusy(true);
    try {
      await DeployApi.applyAddTable(deploymentId, buildSpec());
      message.success("表已创建，菜单已生成（需授权给操作员）");
      reset();
      onCreated();
      onClose();
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const cols: ColumnsType<Draft> = [
    {
      title: "字段名", dataIndex: "name", width: 130,
      render: (v, _r, i) => (
        <Input size="small" value={v} placeholder="name"
               onChange={(e) => patch(i, { name: e.target.value })} />
      ),
    },
    {
      title: "业务名", dataIndex: "biz_name", width: 120,
      render: (v, _r, i) => (
        <Input size="small" value={v} placeholder="客户名"
               onChange={(e) => patch(i, { biz_name: e.target.value })} />
      ),
    },
    {
      title: "类型", dataIndex: "type", width: 130,
      render: (v, _r, i) => (
        <Select size="small" style={{ width: "100%" }} value={v} options={TYPE_OPTIONS}
                onChange={(val) => patch(i, { type: val })} />
      ),
    },
    {
      title: "主键", dataIndex: "is_pk", width: 55, align: "center",
      render: (v, _r, i) => (
        <Checkbox checked={v} onChange={(e) => patch(i, { is_pk: e.target.checked, nullable: e.target.checked ? false : true })} />
      ),
    },
    {
      title: "自增", dataIndex: "autoincrement", width: 55, align: "center",
      render: (v, _r, i) => (
        <Checkbox checked={v} onChange={(e) => patch(i, { autoincrement: e.target.checked })} />
      ),
    },
    {
      title: "可空", dataIndex: "nullable", width: 55, align: "center",
      render: (v, _r, i) => (
        <Checkbox checked={v} onChange={(e) => patch(i, { nullable: e.target.checked })} />
      ),
    },
    {
      title: "唯一", dataIndex: "unique", width: 55, align: "center",
      render: (v, _r, i) => (
        <Checkbox checked={v} onChange={(e) => patch(i, { unique: e.target.checked })} />
      ),
    },
    {
      title: "外键→表", dataIndex: "ref_table", width: 150,
      render: (v, _r, i) => (
        <Select size="small" allowClear style={{ width: "100%" }} value={v || undefined}
                placeholder="选择已有表"
                options={existingTables.map((t) => ({ value: t, label: t }))}
                onChange={(val) => patch(i, { ref_table: val || "", is_fk: !!val })} />
      ),
    },
    {
      title: "", key: "_del", width: 50,
      render: (_v, _r, i) => (
        <Button size="small" danger type="link"
                onClick={() => setRows((prev) => prev.filter((_, idx) => idx !== i))}>
          删除
        </Button>
      ),
    },
  ];

  return (
    <Modal
      title="新建表（工程室）"
      open={open}
      onCancel={() => { reset(); onClose(); }}
      width={980}
      footer={null}
      destroyOnClose
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        message="生成即提案：先「生成提案」审查建表语句，确认后才真正建表并生成菜单（默认不授权给操作员）。"
      />
      <Space wrap style={{ marginBottom: 12 }}>
        <Input addonBefore="描述" style={{ width: 420 }} value={nlDesc}
               placeholder="一句话：客户回访记录表，关联客户"
               onChange={(e) => setNlDesc(e.target.value)} />
        <Button onClick={onNlGenerate} loading={busy}>一句话生成</Button>
      </Space>
      <Space wrap style={{ marginBottom: 12 }}>
        <Input addonBefore="表名" style={{ width: 240 }} value={tableName}
               placeholder="visits" onChange={(e) => setTableName(e.target.value)} />
        <Input addonBefore="业务名" style={{ width: 240 }} value={bizName}
               placeholder="客户回访" onChange={(e) => setBizName(e.target.value)} />
        <Button onClick={() => setRows((prev) => [...prev, emptyRow()])}>+ 增加字段</Button>
      </Space>
      <Table<Draft>
        rowKey={(_r, i) => String(i)}
        size="small"
        pagination={false}
        dataSource={rows}
        columns={cols}
        scroll={{ x: "max-content" }}
      />
      <div style={{ marginTop: 16 }}>
        <Space>
          <Button type="primary" onClick={onPropose} loading={busy}>生成提案</Button>
          {proposal && (
            <Button type="primary" danger onClick={onApply} loading={busy}>
              确认建表
            </Button>
          )}
        </Space>
      </div>
      {proposal && (
        <div style={{ marginTop: 12 }}>
          <Text strong>建表语句（确认执行）</Text>
          <pre style={{ background: "#fafafa", padding: 8, borderRadius: 6, maxHeight: 160, overflow: "auto" }}>
            {proposal.statements.join("\n")}
          </pre>
          <Text type="secondary">
            将生成 {proposal.pages.length} 个页面；共 {proposal.columns.length} 列
          </Text>
        </div>
      )}
    </Modal>
  );
}
