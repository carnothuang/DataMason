import { useEffect, useMemo, useState } from "react";
import {
  App,
  Button,
  Drawer,
  Input,
  InputNumber,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { ColumnOp, DeployApi, FieldColumn } from "../../api";

const { Text } = Typography;

const TYPE_OPTIONS = [
  { value: "INTEGER", label: "整数 INTEGER" },
  { value: "BIGINT", label: "长整数 BIGINT" },
  { value: "VARCHAR", label: "短文本 VARCHAR" },
  { value: "TEXT", label: "长文本 TEXT" },
  { value: "BOOLEAN", label: "布尔 BOOLEAN" },
  { value: "DATETIME", label: "日期时间 DATETIME" },
  { value: "DECIMAL", label: "金额 DECIMAL" },
];

const NAME_RE = /^[a-zA-Z_][a-zA-Z0-9_]*$/;
const OWNERSHIP = new Set(["created_by", "created_at", "updated_by", "updated_at"]);

interface DraftRow {
  key: string;
  origName: string; // 已存在列的原名；新增列为 ""
  name: string;
  bizName: string;
  type: string;
  length: number | null;
  origType: string;
  origLength: number | null;
  locked: boolean;
  removed: boolean;
  isNew: boolean;
}

interface Props {
  deploymentId: number;
  tableName: string;
  bizName: string;
  open: boolean;
  onClose: () => void;
  onChanged: () => void;
}

let seq = 0;

export default function FieldEditor({ deploymentId, tableName, bizName, open, onClose, onChanged }: Props) {
  const { message } = App.useApp();
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [rows, setRows] = useState<DraftRow[]>([]);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    DeployApi.blueprint(deploymentId)
      .then((list) => {
        const t = list.find((x) => x.name === tableName);
        if (!t) throw new Error("未找到该表的字段蓝图");
        setRows(t.columns.map((c: FieldColumn) => toDraft(c)));
      })
      .catch((e) => message.error(e.message))
      .finally(() => setLoading(false));
  }, [open, deploymentId, tableName]);

  function toDraft(c: FieldColumn): DraftRow {
    const locked = c.is_pk || c.is_ownership || c.is_fk || OWNERSHIP.has(c.name);
    return {
      key: `e-${c.name}`,
      origName: c.name,
      name: c.name,
      bizName: c.biz_name || "",
      type: c.type,
      length: c.length ?? null,
      origType: c.type,
      origLength: c.length ?? null,
      locked,
      removed: false,
      isNew: false,
    };
  }

  const update = (key: string, patch: Partial<DraftRow>) =>
    setRows((rs) => rs.map((r) => (r.key === key ? { ...r, ...patch } : r)));

  const addRow = () =>
    setRows((rs) => [
      ...rs,
      {
        key: `n-${Date.now()}-${seq++}`,
        origName: "",
        name: "",
        bizName: "",
        type: "VARCHAR",
        length: 255,
        origType: "VARCHAR",
        origLength: 255,
        locked: false,
        removed: false,
        isNew: true,
      },
    ]);

  const visibleRows = rows.filter((r) => !r.removed);

  const errors = useMemo(() => {
    const errs: Record<string, string> = {};
    const names = new Map<string, number>();
    for (const r of visibleRows) {
      const n = r.name.trim();
      if (!n) errs[r.key] = "列名必填";
      else if (!NAME_RE.test(n)) errs[r.key] = "只能字母/下划线开头，含字母数字下划线";
      else names.set(n, (names.get(n) || 0) + 1);
      if (r.type === "VARCHAR" && (!r.length || r.length < 1 || r.length > 4000))
        errs[r.key] = errs[r.key] || "VARCHAR 长度需 1-4000";
    }
    for (const [n, cnt] of names) if (cnt > 1) names.set(n, cnt);
    const dup = new Set([...names].filter(([, c]) => c > 1).map(([n]) => n));
    for (const r of visibleRows) if (dup.has(r.name.trim())) errs[r.key] = "列名重复";
    return errs;
  }, [visibleRows]);

  const buildOps = (): ColumnOp[] | null => {
    if (Object.keys(errors).length) return null;
    const ops: ColumnOp[] = [];
    for (const r of rows) {
      if (r.isNew) {
        if (r.removed) continue;
        ops.push({
          action: "add",
          name: r.name.trim(),
          biz_name: r.bizName.trim(),
          type: r.type,
          length: r.type === "VARCHAR" ? r.length : null,
        });
      } else if (r.removed) {
        ops.push({ action: "drop", name: r.origName });
      } else if (!r.locked) {
        const newName = r.name.trim();
        if (newName !== r.origName) {
          ops.push({ action: "rename", name: r.origName, new_name: newName });
        }
        const lenChanged =
          r.type === "VARCHAR" ? r.length !== r.origLength : false;
        if (r.type !== r.origType || lenChanged) {
          ops.push({
            action: "change_type",
            name: newName,
            type: r.type,
            length: r.type === "VARCHAR" ? r.length : null,
          });
        }
      }
    }
    return ops;
  };

  const dirtyCount = useMemo(() => {
    const ops = buildOps();
    return ops ? ops.length : -1;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, errors]);

  const save = async () => {
    const ops = buildOps();
    if (!ops) {
      message.error("请先修正标红的字段");
      return;
    }
    if (ops.length === 0) {
      message.info("没有变更");
      return;
    }
    setSaving(true);
    try {
      await DeployApi.alterColumns(deploymentId, tableName, ops);
      message.success("字段已更新");
      onChanged();
      onClose();
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const columns: ColumnsType<DraftRow> = [
    {
      title: "字段名（英文）",
      dataIndex: "name",
      width: 200,
      render: (_v, r) =>
        r.locked ? (
          <Space size={4}>
            <Text code>{r.name}</Text>
            {r.origName === "id" ? <Tag color="gold">主键</Tag> : null}
            {OWNERSHIP.has(r.origName) ? <Tag>系统</Tag> : null}
          </Space>
        ) : (
          <Input
            value={r.name}
            size="small"
            status={errors[r.key] ? "error" : undefined}
            placeholder="如 email"
            onChange={(e) => update(r.key, { name: e.target.value })}
          />
        ),
    },
    {
      title: "业务名",
      dataIndex: "bizName",
      width: 140,
      render: (_v, r) =>
        r.locked ? (
          <Text type="secondary">{r.bizName || "—"}</Text>
        ) : (
          <Input
            value={r.bizName}
            size="small"
            placeholder="如 邮箱"
            onChange={(e) => update(r.key, { bizName: e.target.value })}
          />
        ),
    },
    {
      title: "类型",
      dataIndex: "type",
      width: 180,
      render: (_v, r) =>
        r.locked ? (
          <Text type="secondary">{r.type}</Text>
        ) : (
          <Select
            size="small"
            style={{ width: "100%" }}
            value={r.type}
            options={TYPE_OPTIONS}
            onChange={(v) =>
              update(r.key, { type: v, length: v === "VARCHAR" ? r.length || 255 : null })
            }
          />
        ),
    },
    {
      title: "长度",
      dataIndex: "length",
      width: 110,
      render: (_v, r) =>
        r.type === "VARCHAR" && !r.locked ? (
          <InputNumber
            size="small"
            min={1}
            max={4000}
            value={r.length ?? undefined}
            style={{ width: "100%" }}
            onChange={(v) => update(r.key, { length: v ?? null })}
          />
        ) : (
          <Text type="secondary">—</Text>
        ),
    },
    {
      title: "操作",
      width: 90,
      render: (_v, r) =>
        r.locked ? (
          <Text type="secondary" style={{ fontSize: 12 }}>
            受保护
          </Text>
        ) : r.isNew ? (
          <Button size="small" type="link" danger onClick={() => update(r.key, { removed: true })}>
            移除
          </Button>
        ) : (
          <Popconfirm
            title="删除该字段？"
            description="字段及现有数据将从表中删除，此操作在数据库层面不可撤销。"
            okText="删除"
            cancelText="取消"
            okButtonProps={{ danger: true }}
            onConfirm={() => update(r.key, { removed: true })}
          >
            <Button size="small" type="link" danger>
              删除
            </Button>
          </Popconfirm>
        ),
    },
  ];

  return (
    <Drawer
      title={`编辑字段 · ${bizName || tableName}（${tableName}）`}
      width={760}
      open={open}
      onClose={onClose}
      destroyOnClose
      extra={
        <Space>
          <Button onClick={onClose}>取消</Button>
          <Button type="primary" loading={saving} onClick={save}>
            保存变更{dirtyCount > 0 ? `（${dirtyCount} 项）` : ""}
          </Button>
        </Space>
      }
    >
      <Space style={{ marginBottom: 12 }}>
        <Button type="dashed" onClick={addRow}>
          + 新增字段
        </Button>
        <Text type="secondary" style={{ fontSize: 12 }}>
          新增字段将可空；主键 / 系统归属列 / 外键列受保护不可改。
        </Text>
      </Space>
      <Table<DraftRow>
        rowKey="key"
        size="small"
        loading={loading}
        dataSource={visibleRows}
        columns={columns}
        pagination={false}
        scroll={{ y: "calc(100vh - 240px)" }}
      />
    </Drawer>
  );
}
