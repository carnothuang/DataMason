import { useEffect, useMemo, useState } from "react";
import {
  App,
  Alert,
  Button,
  Card,
  Empty,
  Input,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { ExportOutlined } from "@ant-design/icons";
import type { CrudRowFilters, RowPage, TableMeta } from "../../api";
import { CrudApi } from "../../api";
import CrudForm from "./CrudForm";
import { LogsModal } from "./LogsPage";
import { maskText, useDemo } from "./demo";

const { Text } = Typography;

interface Props {
  deploymentId: number;
  table: string;
  tableBizName: string;
  role?: string;
  /** M2-7：直连模式标识（create/link），缺归属列时提示降级来源。 */
  dataMode?: string;
  /** M2-6 日志→记录聚焦：非空时按主键定点过滤并高亮，便于回看记录现状。 */
  focusPk?: string | null;
  focusNonce?: number;
}

/** M2-4 工作台 CRUD 列表页：元数据驱动（meta 渲染列），搜索/筛选/排序/分页/新增/编辑/删除。
 *  M2-5：操作员视角默认即「我的记录」（服务端强制，前端加提示），全量数据仅创建人/管理员可见。
 *  M2-6：行级「操作记录」弹窗（记录→日志）+ focusPk 定位（日志→记录）。 */
export default function CrudListPage({
  deploymentId,
  table,
  tableBizName,
  role,
  dataMode,
  focusPk,
  focusNonce,
}: Props) {
  const { message } = App.useApp();
  const { demo, masked } = useDemo();
  // M3-3 演示脱敏：只改展示层；脱敏时禁写，避免把脱敏值回写库
  const readOnlyMask = demo && masked;
  const [meta, setMeta] = useState<TableMeta | null>(null);
  const [rows, setRows] = useState<RowPage | null>(null);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState<{ field?: string; order?: "asc" | "desc" }>({});
  const [search, setSearch] = useState<string | undefined>(undefined);
  const [filter, setFilter] = useState<{ col: string; value: string } | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Record<string, unknown> | null>(null);
  const [focusedPk, setFocusedPk] = useState<string | null>(null);
  const [logsRow, setLogsRow] = useState<Record<string, unknown> | null>(null);
  const [reloadTick, setReloadTick] = useState(0);

  const reload = () => setReloadTick((t) => t + 1);

  useEffect(() => {
    CrudApi.meta(deploymentId)
      .then((m) => setMeta(m[table] || null))
      .catch((e) => message.error(e.message));
  }, [deploymentId, table]);

  const pkName = useMemo(
    () => meta?.columns.find((c) => c.pk)?.name || "id",
    [meta],
  );

  // 日志→记录定位：按主键定点过滤，高亮目标行（设计 4.7 双向跳转）
  useEffect(() => {
    if (!focusNonce || !meta || !pkName || !focusPk) return;
    setFilter({ col: pkName, value: String(focusPk) });
    setFocusedPk(String(focusPk));
    setPage(1);
  }, [focusNonce, meta, pkName, focusPk]);

  useEffect(() => {
    if (!meta) return;
    setLoading(true);
    const params: CrudRowFilters = {
      page,
      page_size: 20,
      sort: sort.field,
      order: sort.order,
      search,
    };
    if (filter) params[filter.col] = filter.value;
    CrudApi.rows(deploymentId, table, params)
      .then(setRows)
      .catch((e) => message.error(e.message))
      .finally(() => setLoading(false));
  }, [deploymentId, table, meta, page, sort.field, sort.order, search, filter, reloadTick]);

  const visibleCols = useMemo(
    () => (meta ? meta.columns.filter((c) => !c.is_ownership) : []),
    [meta],
  );

  const filterableCols = useMemo(
    () => visibleCols.filter((c) => !c.fk || c.fk.ref_table !== table),
    [visibleCols, table],
  );

  const columns: ColumnsType<Record<string, unknown>> = [
    ...(visibleCols.map((c) => ({
      title: c.biz_name || c.name,
      dataIndex: c.name,
      key: c.name,
      ellipsis: true,
      sorter: true,
      sortOrder:
        sort.field === c.name
          ? (sort.order === "desc" ? "descend" : "ascend")
          : null,
      render: (v: unknown) =>
        v === null || v === undefined ? (
          <Text type="secondary">—</Text>
        ) : (
          String(masked ? maskText(v) : v)
        ),
    })) as ColumnsType<Record<string, unknown>>),
    {
      title: "操作",
      key: "_ops",
      width: 220,
      fixed: "right",
      render: (_: unknown, record: Record<string, unknown>) =>
        readOnlyMask ? (
          <Tag>演示脱敏 · 只读</Tag>
        ) : (
          <Space size="small">
            <Button type="link" size="small" onClick={() => openEdit(record)}>
              编辑
            </Button>
            <Button type="link" size="small" onClick={() => setLogsRow(record)}>
              操作记录
            </Button>
            <Popconfirm
              title="确认删除此行？"
              description="删除后不可恢复"
              okText="删除"
              okButtonProps={{ danger: true }}
              onConfirm={() => doDelete(record)}
            >
              <Button type="link" size="small" danger>
                删除
              </Button>
            </Popconfirm>
          </Space>
        ),
    },
  ];

  const openCreate = () => {
    setEditing(null);
    setFormOpen(true);
  };

  const openEdit = (record: Record<string, unknown>) => {
    setEditing(record);
    setFormOpen(true);
  };

  const doDelete = async (record: Record<string, unknown>) => {
    try {
      await CrudApi.remove(deploymentId, table, record[pkName] as string | number);
      message.success("已删除");
      reload();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const filterCol = filter ? visibleCols.find((c) => c.name === filter.col) : null;

  // M2-7 缺归属列降级明示：表缺 created_by 时「我的记录」服务端过滤不可用（全量可见）
  const hasCreatedBy = useMemo(
    () => !!meta?.columns.some((c) => c.name === "created_by"),
    [meta],
  );

  return (
    <Card
      title={tableBizName || table}
      extra={
        !readOnlyMask ? (
          <Button type="primary" onClick={openCreate}>
            新增
          </Button>
        ) : null
      }
    >
      {!demo && role === "operator" && hasCreatedBy && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="我的记录：这里只显示您录入的数据（全量数据归创建人与管理员）"
        />
      )}
      {!demo && role === "operator" && !hasCreatedBy && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message={
            dataMode === "link"
              ? "该表来自外部直连库且缺少归属列 created_by，「我的记录」降级不可用，此处展示全量数据（DataMason 绝不修改外部库结构）"
              : "该表缺少归属列 created_by，「我的记录」不可用，此处展示全量数据"
          }
        />
      )}
      {!demo && focusedPk && (
        <Alert
          type="success"
          showIcon
          style={{ marginBottom: 12 }}
          message={`已从操作日志定位到记录 #${focusedPk}（下方按主键过滤）`}
          action={
            <Button
              size="small"
              onClick={() => {
                setFocusedPk(null);
                setFilter(null);
              }}
            >
              恢复全部
            </Button>
          }
        />
      )}
      {!meta ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="元数据加载中…" />
      ) : (
        <Space direction="vertical" style={{ width: "100%" }} size="middle">
          <Space wrap>
            <Input.Search
              allowClear
              placeholder="全局搜索"
              style={{ width: 240 }}
              onSearch={(v) => {
                setSearch(v.trim() || undefined);
                setPage(1);
              }}
            />
            <Select
              allowClear
              placeholder="选择筛选列"
              style={{ width: 150 }}
              value={filter?.col}
              options={filterableCols.map((c) => ({ value: c.name, label: c.biz_name || c.name }))}
              onChange={(v) =>
                setFilter(v ? { col: v as string, value: "" } : null)
              }
            />
            {filter && filterCol && (
              <Input
                allowClear
                style={{ width: 200 }}
                placeholder={`按「${filterCol.biz_name || filterCol.name}」筛选`}
                value={filter.value}
                onChange={(e) => setFilter({ col: filter.col, value: e.target.value })}
              />
            )}
            {filter && (
              <Tag closable onClose={() => setFilter(null)}>
                <Text strong>{filterCol?.biz_name || filter.col}</Text>
                {filter.value ? ` = ${filter.value}` : ""}
              </Tag>
            )}
            <Button
              icon={<ExportOutlined />}
              onClick={() => {
                const params: CrudRowFilters = {};
                if (search) params.search = search;
                if (filter?.col && filter.value !== "") params[filter.col] = filter.value;
                if (sort.field) {
                  params.sort = sort.field;
                  params.order = sort.order;
                }
                window.open(CrudApi.exportCsvUrl(deploymentId, table, params), "_blank");
              }}
            >
              导出 CSV
            </Button>
          </Space>
          <Table
            rowKey={(r) => String(r[pkName] ?? JSON.stringify(r))}
            loading={loading}
            dataSource={rows?.data || []}
            columns={columns}
            size="middle"
            scroll={{ x: "max-content" }}
            onRow={(r) =>
              focusedPk && String(r[pkName]) === focusedPk
                ? { style: { background: "#fff1f0" } }
                : {}
            }
            pagination={{
              current: page,
              pageSize: 20,
              total: rows?.total || 0,
              showTotal: (t) => `共 ${t} 条`,
              onChange: (p) => setPage(p),
            }}
            onChange={(_p, _f, sorter) => {
              const s = Array.isArray(sorter) ? sorter[0] : sorter;
              if (s && s.field && s.order) {
                setSort({ field: String(s.field), order: s.order === "descend" ? "desc" : "asc" });
              } else {
                setSort({});
              }
            }}
          />
        </Space>
      )}
      <CrudForm
        deploymentId={deploymentId}
        table={table}
        meta={meta?.columns || []}
        pkName={pkName}
        initial={editing}
        open={formOpen}
        onClose={() => setFormOpen(false)}
        onSaved={reload}
      />
      {logsRow && (
        <LogsModal
          open={!!logsRow}
          onClose={() => setLogsRow(null)}
          deploymentId={deploymentId}
          table={table}
          pk={String(logsRow[pkName])}
          tableBizName={tableBizName}
        />
      )}
    </Card>
  );
}