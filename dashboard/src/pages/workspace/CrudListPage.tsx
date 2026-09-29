import { useEffect, useMemo, useState } from "react";
import {
  App,
  Alert,
  Button,
  Card,
  Descriptions,
  Empty,
  Input,
  List,
  Popconfirm,
  Segmented,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  AppstoreOutlined,
  BarsOutlined,
  ExportOutlined,
} from "@ant-design/icons";
import type {
  CrudRowFilters,
  FormFieldConfig,
  LayoutBlockProps,
  RowPage,
  TableMeta,
  TableUiConfigPayload,
} from "../../api";
import { CrudApi, FormsApi } from "../../api";
import CrudForm from "./CrudForm";
import { LogsModal } from "./LogsPage";
import { useDemo } from "./demo";
import FileChip from "../../components/FileChip";
import ValueLabel from "../../components/ValueLabel";
import ImportWizard from "./ImportWizard";

const { Text } = Typography;

interface Props {
  deploymentId: number;
  table: string;
  tableBizName: string;
  role?: string;
  /** M2-7：直连模式标识（create/link），缺归属列时提示降级来源。 */
  dataMode?: string;
  /** 0.11.0 M5-C：该页允许的动作（view/create/update/delete/export/import）；
   *  undefined = 不限制（兼容旧链路/服务身份）。 */
  actions?: string[];
  /** 0.11.0 M5-C：数据范围（all/own），own 时服务端只返回本人记录。 */
  dataScope?: string;
  /** M2-6 日志→记录聚焦：非空时按主键定点过滤并高亮，便于回看记录现状。 */
  focusPk?: string | null;
  focusNonce?: number;
  /** 0.15.0 M9-8：作为页面布局区块渲染时的区块配置与表级 UI 配置（合并优先级：区块 > 表级 > 默认）。 */
  blockProps?: LayoutBlockProps;
  uiConfig?: TableUiConfigPayload;
}

/** M2-4 工作台 CRUD 列表页：元数据驱动（meta 渲染列），搜索/筛选/排序/分页/新增/编辑/删除。
 *  M2-5：操作员视角默认即「我的记录」（服务端强制，前端加提示），全量数据仅创建人/管理员可见。
 *  M2-6：行级「操作记录」弹窗（记录→日志）+ focusPk 定位（日志→记录）。
 *  M5-C：按 actions/dataScope 裁剪新增/编辑/删除/导出，只读时服务端仍强制。 */
export default function CrudListPage({
  deploymentId,
  table,
  tableBizName,
  role,
  dataMode,
  actions,
  dataScope,
  focusPk,
  focusNonce,
  blockProps,
  uiConfig,
}: Props) {
  const { message } = App.useApp();
  const { demo, masked } = useDemo();
  // M3-3 演示脱敏：只改展示层；脱敏时禁写，避免把脱敏值回写库
  const readOnlyMask = demo && masked;
  const can = (action: string) => !actions || actions.includes(action);
  const [meta, setMeta] = useState<TableMeta | null>(null);
  const [rows, setRows] = useState<RowPage | null>(null);
  // 0.15.0 M9-5：运行态表单配置（控件类型/候选项），失败静默降级（默认映射回显）
  const [formConfig, setFormConfig] = useState<Record<string, FormFieldConfig>>({});
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState<{ field?: string; order?: "asc" | "desc" }>({});
  const [search, setSearch] = useState<string | undefined>(undefined);
  const [filter, setFilter] = useState<{ col: string; value: string } | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Record<string, unknown> | null>(null);
  const [focusedPk, setFocusedPk] = useState<string | null>(null);
  const [logsRow, setLogsRow] = useState<Record<string, unknown> | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [exportCols, setExportCols] = useState<string[]>([]);
  const [viewMode, setViewMode] = useState<"table" | "card">("table");
  const [reloadTick, setReloadTick] = useState(0);

  const reload = () => setReloadTick((t) => t + 1);

  useEffect(() => {
    CrudApi.meta(deploymentId)
      .then((m) => setMeta(m[table] || null))
      .catch((e) => message.error(e.message));
  }, [deploymentId, table]);

  // 0.15.0 M9-5：拉运行态配置驱动列表/筛选回显；失败静默降级（不阻断）
  useEffect(() => {
    let alive = true;
    setFormConfig({});
    FormsApi.runtimeConfig(deploymentId, table)
      .then((cfg) => {
        if (!alive) return;
        const m = new Map<string, FormFieldConfig>();
        for (const f of cfg.fields || []) m.set(f.field_name, f);
        setFormConfig(Object.fromEntries(m));
      })
      .catch(() => {
        /* 配置不可用 → 列表/筛选回退默认映射，零配置零变化 */
      });
    return () => {
      alive = false;
    };
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

  // 0.15.0 M9-8：合并列表配置（区块 props > 表级 ui_config.list > 默认）
  const listCfg = useMemo(() => {
    const l = uiConfig?.list || {};
    return {
      visibleColumns: blockProps?.columns ?? l.visible_columns,
      colWidths: { ...(l.col_widths || {}), ...(blockProps?.col_widths || {}) },
      showActions: blockProps?.show_actions ?? l.show_actions,
      defaultView: blockProps?.default_view ?? l.default_view,
      showFilters: blockProps?.show_filters ?? l.show_filters,
    };
  }, [blockProps, uiConfig]);

  // 0.15.0 M9-8：默认视图读配置（"card" 时初始进入卡片视图）；用户手动切换后不再覆盖
  useEffect(() => {
    if (listCfg.defaultView === "card") setViewMode("card");
    else if (listCfg.defaultView === "table") setViewMode("table");
  }, [listCfg.defaultView]);

  // 配置指定列显隐：按配置顺序保留存在的列；未配置 = 全部
  const shownColumns = useMemo(() => {
    const wanted = listCfg.visibleColumns;
    if (!wanted?.length) return visibleCols;
    const byName = new Map(visibleCols.map((c) => [c.name, c]));
    return wanted.map((n) => byName.get(n)).filter((c): c is (typeof visibleCols)[number] => !!c);
  }, [visibleCols, listCfg.visibleColumns]);

  const filterableCols = useMemo(
    () => shownColumns.filter((c) => !c.fk || c.fk.ref_table !== table),
    [shownColumns, table],
  );

  const renderCellValue = (c: (typeof shownColumns)[number], v: unknown) =>
    c.type === "FILE" ? (
      <FileChip deploymentId={deploymentId} value={v} masked={masked} />
    ) : (
      <ValueLabel
        value={v}
        controlType={formConfig[c.name]?.control_type ?? null}
        options={formConfig[c.name]?.options}
        refLabels={c.fk ? rows?.ref_labels?.[c.name] : undefined}
        masked={masked}
      />
    );

  const renderRowActions = (record: Record<string, unknown>) => {
    if (readOnlyMask) return <Tag>演示脱敏 · 只读</Tag>;

    return (
      <Space size="small">
        {can("update") && (
          <Button type="link" size="small" onClick={() => openEdit(record)}>
            编辑
          </Button>
        )}
        <Button type="link" size="small" onClick={() => setLogsRow(record)}>
          操作记录
        </Button>
        {can("delete") && (
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
        )}
      </Space>
    );
  };

  const columns: ColumnsType<Record<string, unknown>> = [
    ...(shownColumns.map((c) => ({
      title: c.biz_name || c.name,
      dataIndex: c.name,
      key: c.name,
      ellipsis: true,
      width: listCfg.colWidths[c.name],
      sorter: true,
      sortOrder:
        sort.field === c.name
          ? (sort.order === "desc" ? "descend" : "ascend")
          : null,
      render: (v: unknown) => renderCellValue(c, v),
    })) as ColumnsType<Record<string, unknown>>),
    ...(listCfg.showActions === false
      ? []
      : [
          {
            title: "操作",
            key: "_ops",
            width: 220,
            fixed: "right" as const,
            render: (_: unknown, record: Record<string, unknown>) =>
              renderRowActions(record),
          },
        ]),
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

  // 0.15.0 M9-5：静态选项字段（radio/select/cascader/checkbox 有候选项）筛选改为下拉
  // （显示 label、提交 value，过滤仍按库存值）；其余字段维持文本输入。
  const filterColCfg = filterCol ? formConfig[filterCol.name] : undefined;
  const filterIsOption =
    !!filterCol &&
    !!filterColCfg?.options?.length &&
    ["radio", "select", "cascader", "checkbox"].includes(filterColCfg.control_type || "");

  // M2-7 缺归属列降级明示：表缺 created_by 时「我的记录」服务端过滤不可用（全量可见）
  const hasCreatedBy = useMemo(
    () => !!meta?.columns.some((c) => c.name === "created_by"),
    [meta],
  );

  return (
    <Card
      title={tableBizName || table}
      style={{ height: "100%", display: "flex", flexDirection: "column" }}
      styles={{
        body: {
          flex: 1,
          minHeight: 0,
          display: "flex",
          flexDirection: "column",
          overflow: "auto",
        },
      }}
      extra={
        !readOnlyMask && can("create") ? (
          <Button type="primary" onClick={openCreate}>
            新增
          </Button>
        ) : null
      }
    >
      {!demo && role === "operator" && dataScope !== "all" && hasCreatedBy && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="我的记录：这里只显示您录入的数据（全量数据归创建人与管理员）"
        />
      )}
      {!demo && role === "operator" && dataScope !== "all" && !hasCreatedBy && (
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
        <Space
          direction="vertical"
          style={{ width: "100%", flex: 1, minHeight: 0 }}
          size="middle"
        >
          <div
            style={{
              display: "flex",
              justifyContent: "flex-end",
              marginBottom: 4,
            }}
          >
            <Segmented
              value={viewMode}
              onChange={(v) => setViewMode(v as "table" | "card")}
              options={[
                { value: "table", icon: <BarsOutlined /> },
                { value: "card", icon: <AppstoreOutlined /> },
              ]}
            />
          </div>
          <Space wrap style={{ display: listCfg.showFilters === false ? "none" : undefined }}>
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
              filterIsOption ? (
                <Select
                  allowClear
                  showSearch
                  style={{ width: 200 }}
                  placeholder={`按「${filterCol.biz_name || filterCol.name}」筛选`}
                  value={filter.value || undefined}
                  options={(formConfig[filterCol.name]?.options || []).map((o) => ({
                    value: o.value,
                    label: o.label,
                  }))}
                  onChange={(v) => setFilter({ col: filter.col, value: v ?? "" })}
                />
              ) : (
                <Input
                  allowClear
                  style={{ width: 200 }}
                  placeholder={`按「${filterCol.biz_name || filterCol.name}」筛选`}
                  value={filter.value}
                  onChange={(e) => setFilter({ col: filter.col, value: e.target.value })}
                />
              )
            )}
            {filter && (
              <Tag closable onClose={() => setFilter(null)}>
                <Text strong>{filterCol?.biz_name || filter.col}</Text>
                {filter.value ? ` = ${filter.value}` : ""}
              </Tag>
            )}
            {can("export") && (
              <Select
                mode="multiple"
                allowClear
                placeholder="导出列（默认全部）"
                style={{ minWidth: 180 }}
                value={exportCols}
                options={shownColumns.map((c) => ({ value: c.name, label: c.biz_name || c.name }))}
                onChange={(v) => setExportCols(v as string[])}
              />
            )}
            {can("export") && (
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
                  if (exportCols.length) params.cols = exportCols.join(",");
                  window.open(CrudApi.exportCsvUrl(deploymentId, table, params), "_blank");
                }}
              >
                导出 CSV
              </Button>
            )}
            {can("export") && (
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
                  if (exportCols.length) params.cols = exportCols.join(",");
                  window.open(CrudApi.exportXlsxUrl(deploymentId, table, params), "_blank");
                }}
              >
                导出 Excel
              </Button>
            )}
            {can("import") && (
              <Button onClick={() => setImportOpen(true)}>导入</Button>
            )}
          </Space>
          <div style={{ flex: 1, minHeight: 0 }}>
            {viewMode === "table" ? (
              <Table
                rowKey={(r) => String(r[pkName] ?? JSON.stringify(r))}
                loading={loading}
                dataSource={rows?.data || []}
                columns={columns}
                size="middle"
                scroll={{ x: "max-content" }}
                onRow={(r) =>
                  focusedPk && String(r[pkName]) === focusedPk
                    ? { style: { background: "var(--fn-bg-danger-light)" } }
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
            ) : (
              <List
                rowKey={(r) => String(r[pkName] ?? JSON.stringify(r))}
                loading={loading}
                grid={{ gutter: 12, xs: 1, sm: 1, md: 2, lg: 2, xl: 3, xxl: 3 }}
                dataSource={rows?.data || []}
                locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无记录" /> }}
                renderItem={(record) => (
                  <List.Item>
                    <Card
                      size="small"
                      title={
                        shownColumns[0]
                          ? renderCellValue(shownColumns[0], record[shownColumns[0].name])
                          : tableBizName
                      }
                      style={
                        focusedPk && String(record[pkName]) === focusedPk
                          ? { background: "var(--fn-bg-danger-light)" }
                          : undefined
                      }
                      styles={{ body: { maxHeight: 320, overflow: "auto" } }}
                    >
                      <Descriptions column={1} size="small" colon={false}>
                        {shownColumns.slice(1).map((c) => (
                          <Descriptions.Item key={c.name} label={c.biz_name || c.name}>
                            {renderCellValue(c, record[c.name])}
                          </Descriptions.Item>
                        ))}
                      </Descriptions>
                      {listCfg.showActions !== false && renderRowActions(record)}
                    </Card>
                  </List.Item>
                )}
                pagination={{
                  current: page,
                  pageSize: 20,
                  total: rows?.total || 0,
                  showTotal: (t) => `共 ${t} 条`,
                  onChange: (p) => setPage(p),
                }}
              />
            )}
          </div>
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
      <ImportWizard
        deploymentId={deploymentId}
        table={table}
        tableBizName={tableBizName}
        open={importOpen}
        onClose={() => setImportOpen(false)}
        onImported={reload}
      />
    </Card>
  );
}