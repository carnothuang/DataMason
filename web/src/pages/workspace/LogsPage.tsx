import { useEffect, useMemo, useState } from "react";
import { App, Button, Modal, Select, Space, Spin, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { DownloadOutlined } from "@ant-design/icons";
import DatePicker from "antd/es/date-picker";
import type { ActivityLog, LogFilters } from "../../api";
import { DeployApi, downloadJson, LogsApi } from "../../api";
import type { Dayjs } from "dayjs";

const { Text } = Typography;
const { RangePicker } = DatePicker;

export const ACTION_META: Record<string, { label: string; color: string }> = {
  create: { label: "新建", color: "green" },
  update: { label: "更新", color: "blue" },
  delete: { label: "删除", color: "red" },
  deploy: { label: "部署", color: "gold" },
  member: { label: "成员", color: "purple" },
  structure: { label: "结构变更", color: "orange" },
  flow: { label: "流程", color: "cyan" },
  apikey: { label: "密钥", color: "geekblue" },
};

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

/** 时间戳展示：今天/昨天的相对称呼 + HH:mm，再退到 MM-DD 或 YYYY-MM-DD（设计 4.7：不露技术标识）。 */
export function fmtTime(occ: string): string {
  const d = new Date(occ.replace(" ", "T"));
  if (Number.isNaN(d.getTime())) return occ;
  const now = new Date();
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const sameDay = (a: Date, b: Date) =>
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate();
  if (sameDay(d, now)) return `今天 ${hm}`;
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (sameDay(d, yesterday)) return `昨天 ${hm}`;
  const md = `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${hm}`;
  return d.getFullYear() === now.getFullYear()
    ? md
    : `${d.getFullYear()}-${md}`;
}

function fmtVal(v: unknown): string {
  if (v === null || v === undefined || v === "") return "空";
  return String(v);
}

/** 一行的人话摘要（列表不让露出技术标识，字段级明细收进展开层）。 */
export function logSummary(lg: ActivityLog): string {
  if (lg.action === "create") return `新建了「${lg.object_label}」`;
  if (lg.action === "update") {
    const parts = Object.entries(lg.changes).map(
      ([field, c]) => `${field} ${fmtVal(c.old)} → ${fmtVal(c.new)}`,
    );
    return `更新了「${lg.object_label}」${parts.length ? `：${parts.join("；")}` : ""}`;
  }
  if (lg.action === "delete") return `删除了「${lg.object_label}」`;
  return lg.object_label;
}

interface LogsPageProps {
  deploymentId: number;
  onViewRecord: (table: string, pk: string) => void;
}

export default function LogsPage({ deploymentId, onViewRecord }: LogsPageProps) {
  const { message } = App.useApp();
  const [logs, setLogs] = useState<ActivityLog[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [action, setAction] = useState<string | undefined>(undefined);
  const [actorId, setActorId] = useState<number | undefined>(undefined);
  const [tableName, setTableName] = useState<string | undefined>(undefined);
  const [range, setRange] = useState<[Dayjs, Dayjs] | null>(null);
  const [exporting, setExporting] = useState(false);

  // 筛选下拉候选项：随已加载数据累积（接口不单独暴露候选人/表清单）
  const [people, setPeople] = useState<{ id: number; name: string }[]>([]);
  const [tables, setTables] = useState<{ name: string; biz: string }[]>([]);

  useEffect(() => {
    setLoading(true);
    const params: LogFilters = { page, page_size: 20 };
    if (action) params.action = action;
    if (actorId !== undefined) params.actor_user_id = actorId;
    if (tableName) params.object_table = tableName;
    if (range?.[0]) params.from = range[0].format("YYYY-MM-DD HH:mm:ss");
    if (range?.[1]) params.to = range[1].format("YYYY-MM-DD HH:mm:ss");
    LogsApi.list(deploymentId, params)
      .then((res) => {
        setLogs(res.logs);
        setTotal(res.total);
        setPeople((prev) => {
          const map = new Map(prev.map((p) => [p.id, p]));
          res.logs.forEach((lg) => {
            if (lg.actor_user_id != null && lg.actor_display) {
              map.set(lg.actor_user_id, { id: lg.actor_user_id, name: lg.actor_display });
            }
          });
          return [...map.values()];
        });
        setTables((prev) => {
          const map = new Map(prev.map((t) => [t.name, t]));
          res.logs.forEach((lg) => {
            if (lg.object_table) {
              map.set(lg.object_table, {
                name: lg.object_table,
                biz: lg.object_biz_name || lg.object_table,
              });
            }
          });
          return [...map.values()];
        });
      })
      .catch((e) => message.error(e.message))
      .finally(() => setLoading(false));
  }, [deploymentId, page, action, actorId, tableName, range]);

  const resetPage = () => setPage(1);

  // M3-6：导出整包（配置 + 全量日志）为可离线打开的 JSON
  const onExport = async () => {
    setExporting(true);
    try {
      const pkg = await DeployApi.exportPackage(deploymentId);
      downloadJson(`datamason-deploy-${deploymentId}.json`, pkg);
      message.success("已导出配置包（含全量操作日志）");
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setExporting(false);
    }
  };

  const personOptions = useMemo(
    () => people.map((p) => ({ label: p.name, value: p.id })),
    [people],
  );
  const tableOptions = useMemo(
    () => tables.map((t) => ({ label: t.biz, value: t.name })),
    [tables],
  );

  const columns: ColumnsType<ActivityLog> = [
    {
      title: "时间",
      dataIndex: "occurred_at",
      key: "occurred_at",
      width: 130,
      render: (v: string) => <Text>{fmtTime(v)}</Text>,
    },
    {
      title: "动作",
      dataIndex: "action",
      key: "action",
      width: 96,
      render: (a: string) => (
        <Tag color={ACTION_META[a]?.color || "default"}>{ACTION_META[a]?.label || a}</Tag>
      ),
    },
    {
      title: "人",
      dataIndex: "actor_display",
      key: "actor_display",
      width: 120,
      ellipsis: true,
    },
    {
      title: "业务对象",
      dataIndex: "object_biz_name",
      key: "object_biz_name",
      width: 130,
      ellipsis: true,
      render: (v: string) => (v ? <Text>{v}</Text> : <Text type="secondary">系统</Text>),
    },
    {
      title: "摘要",
      dataIndex: "object_label",
      key: "summary",
      ellipsis: true,
      render: (_: unknown, lg: ActivityLog) => <Text>{logSummary(lg)}</Text>,
    },
    {
      title: "操作",
      key: "_ops",
      width: 120,
      fixed: "right",
      render: (_: unknown, lg: ActivityLog) =>
        lg.object_kind === "table" &&
        lg.object_pk &&
        (lg.action === "create" || lg.action === "update") ? (
          <Button
            type="link"
            size="small"
            onClick={() => onViewRecord(lg.object_table, lg.object_pk)}
          >
            查看这条记录
          </Button>
        ) : null,
    },
  ];

  const renderChanges = (lg: ActivityLog) => {
    const entries = Object.entries(lg.changes);
    if (lg.action === "structure" && Array.isArray(lg.changes.ops)) {
      return (
        <Space wrap size={[8, 4]}>
          {(lg.changes.ops as { action?: string; name?: string }[]).map((op, i) => (
            <Tag key={i} color="orange">
              {op.action || ""} {op.name || ""}
            </Tag>
          ))}
        </Space>
      );
    }
    if (entries.length === 0) {
      return <Text type="secondary">无字段变更记录</Text>;
    }
    return (
      <div>
        {entries.map(([f, c]) => (
          <div key={f} style={{ padding: "2px 0" }}>
            <Text strong>{f}</Text>
            <Text type="secondary" style={{ marginLeft: 8 }}>
              从「{fmtVal(c?.old)}」变为「{fmtVal(c?.new)}」
            </Text>
          </div>
        ))}
      </div>
    );
  };

  return (
    <Space direction="vertical" style={{ width: "100%" }} size="middle">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: 8 }}>
        <Space wrap>
        <span>
          <Text type="secondary">动作</Text>
          <Select
            style={{ width: 140, marginLeft: 8 }}
            placeholder="全部动作"
            allowClear
            options={Object.entries(ACTION_META).map(([v, m]) => ({ value: v, label: m.label }))}
            onChange={(v) => {
              setAction(v);
              resetPage();
            }}
          />
        </span>
        <span>
          <Text type="secondary">人</Text>
          <Select
            style={{ width: 140, marginLeft: 8 }}
            placeholder="全部人"
            allowClear
            showSearch
            optionFilterProp="label"
            options={personOptions}
            onChange={(v) => {
              setActorId(v);
              resetPage();
            }}
          />
        </span>
        <span>
          <Text type="secondary">业务对象</Text>
          <Select
            style={{ width: 140, marginLeft: 8 }}
            placeholder="全部对象"
            allowClear
            showSearch
            optionFilterProp="label"
            options={tableOptions}
            onChange={(v) => {
              setTableName(v);
              resetPage();
            }}
          />
        </span>
        <span>
          <Text type="secondary">时间段</Text>
          <RangePicker
            style={{ marginLeft: 8 }}
            allowEmpty={[true, true]}
            showTime={{ format: "HH:mm" }}
            format="MM-DD HH:mm"
            onChange={(value) => {
              setRange(
                value && value[0] && value[1]
                  ? [value[0] as Dayjs, value[1] as Dayjs]
                  : null,
              );
              resetPage();
            }}
          />
        </span>
        </Space>
        <Button icon={<DownloadOutlined />} loading={exporting} onClick={onExport}>
          导出配置包
        </Button>
      </div>
      <Table<ActivityLog>
        rowKey={(lg) => lg.id}
        loading={loading}
        dataSource={logs}
        columns={columns}
        size="middle"
        scroll={{ x: "max-content" }}
        expandable={{
          expandedRowRender: renderChanges,
          rowExpandable: (lg) => Object.keys(lg.changes).length > 0,
        }}
        pagination={{
          current: page,
          pageSize: 20,
          total,
          showTotal: (t) => `共 ${t} 条`,
          onChange: (p) => setPage(p),
        }}
      />
    </Space>
  );
}

/** 记录→日志方向：行级「操作记录」弹窗（object_table + object_pk 定点查询）。 */
export function LogsModal({
  open,
  onClose,
  deploymentId,
  table,
  pk,
  tableBizName,
}: {
  open: boolean;
  onClose: () => void;
  deploymentId: number;
  table: string;
  pk: string;
  tableBizName?: string;
}) {
  const { message } = App.useApp();
  const [logs, setLogs] = useState<ActivityLog[]>([]);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    setLoaded(false);
    LogsApi.list(deploymentId, {
      object_table: table,
      object_pk: pk,
      page: 1,
      page_size: 50,
    })
      .then((res) => {
        setLogs(res.logs);
        setLoaded(true);
      })
      .catch((e) => message.error(e.message))
      .finally(() => setLoading(false));
  }, [open, deploymentId, table, pk]);

  const columns: ColumnsType<ActivityLog> = [
    {
      title: "时间",
      dataIndex: "occurred_at",
      key: "occurred_at",
      width: 130,
      render: (v: string) => <Text>{fmtTime(v)}</Text>,
    },
    {
      title: "动作",
      dataIndex: "action",
      key: "action",
      width: 90,
      render: (a: string) => (
        <Tag color={ACTION_META[a]?.color || "default"}>{ACTION_META[a]?.label || a}</Tag>
      ),
    },
    {
      title: "人",
      dataIndex: "actor_display",
      key: "actor_display",
      width: 120,
    },
    {
      title: "摘要",
      key: "summary",
      ellipsis: true,
      render: (_: unknown, lg: ActivityLog) => <Text>{logSummary(lg)}</Text>,
    },
  ];

  return (
    <Modal
      open={open}
      onCancel={onClose}
      title={`「${tableBizName || table}」 #${pk} 的操作记录`}
      footer={null}
      width={760}
      destroyOnHidden
    >
      <Spin spinning={loading}>
        <Table<ActivityLog>
          rowKey={(lg) => lg.id}
          size="small"
          columns={columns}
          dataSource={logs}
          pagination={{ pageSize: 10, showTotal: (t) => `共 ${t} 条` }}
          locale={{
            emptyText: loaded ? "暂无操作记录" : "加载中…",
          }}
        />
      </Spin>
    </Modal>
  );
}