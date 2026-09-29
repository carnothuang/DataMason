import { useCallback, useEffect, useMemo, useState } from "react";
import {
  App,
  Button,
  Empty,
  Popconfirm,
  Segmented,
  Select,
  Spin,
} from "antd";
import {
  AppstoreOutlined,
  CheckCircleFilled,
  ClockCircleOutlined,
  CloseCircleFilled,
  DeleteOutlined,
  ReloadOutlined,
  ThunderboltOutlined,
  UnorderedListOutlined,
} from "@ant-design/icons";
import { AiGenApi, AiGenerationItem, AiGenerationsPage } from "../../api";
import { ResizableTable } from "../../components/ResizableTable";
import { useTranslation } from "react-i18next";
import "./shared.css";

const VIEW_KEY = "datamason:ai:view";
const TABLE_WIDTH_KEY = "datamason:ai:table-widths";

type ViewMode = "card" | "table";

const KIND_KEY: Record<string, string> = {
  deploy: "ai.kindDeploy",
  table: "ai.kindTable",
  seed: "ai.kindSeed",
  crud: "ai.kindCrud",
  qa: "ai.kindQa",
};

const KIND_TONE: Record<string, string> = {
  deploy: "#2f54eb",
  table: "#13a8a8",
  seed: "#52c41a",
  crud: "#722ed1",
  qa: "#fa8c16",
};

function kindTone(kind: string): string {
  return KIND_TONE[kind] || "#8c8c8c";
}

export function kindLabel(kind: string): string {
  return KIND_KEY[kind] ? KIND_KEY[kind] : kind;
}

interface GenerationTableProps {
  data: AiGenerationsPage | null;
  loading: boolean;
  onRemove?: (item: AiGenerationItem) => void;
  storageKey?: string;
}

export function GenerationTable({
  data,
  loading,
  onRemove,
  storageKey = TABLE_WIDTH_KEY,
}: GenerationTableProps) {
  const { t } = useTranslation();
  const items = data?.items || [];

  const removeColumn = onRemove
    ? [
        {
          key: "action",
          title: t("ai.colActions"),
          width: 80,
          render: (_: unknown, r: AiGenerationItem) => (
            <Popconfirm
              title={t("ai.deleteConfirm")}
              onConfirm={() => onRemove(r)}
            >
              <button
                type="button"
                className="dm-set-icon-btn dm-set-icon-btn-danger"
                title={t("ai.colActions")}
              >
                <DeleteOutlined />
              </button>
            </Popconfirm>
          ),
        },
      ]
    : [];

  const columns = [
    {
      key: "created_at",
      title: t("ai.colTime"),
      dataIndex: "created_at",
      width: 170,
      render: (v: string) => <span className="dm-set-cell-muted">{v}</span>,
    },
    {
      key: "kind",
      title: t("ai.colKind"),
      dataIndex: "kind",
      width: 130,
      render: (k: string) => (
        <span
          className="dm-set-pill"
          style={{ color: kindTone(k), background: `${kindTone(k)}14` }}
        >
          {t(kindLabel(k))}
        </span>
      ),
    },
    {
      key: "status",
      title: t("ai.colStatus"),
      dataIndex: "status",
      width: 90,
      render: (s: string) =>
        s === "ok" ? (
          <span className="dm-set-pill">
            <CheckCircleFilled style={{ color: "#52c41a" }} />
            {t("ai.statusOk")}
          </span>
        ) : (
          <span className="dm-set-pill">
            <CloseCircleFilled style={{ color: "#ff4d4f" }} />
            {t("ai.statusError")}
          </span>
        ),
    },
    {
      key: "deployment_name",
      title: t("ai.colDeployment"),
      dataIndex: "deployment_name",
      width: 150,
      render: (v: string) => (
        <span className="dm-set-cell-muted">{v || "—"}</span>
      ),
    },
    {
      key: "summary",
      title: t("ai.colSummary"),
      render: (_: unknown, r: AiGenerationItem) => (
        <div className="dm-set-summary">
          <span className="dm-set-summary-prompt">
            {r.prompt_summary || "—"}
          </span>
          {r.result_summary && (
            <span className="dm-set-summary-result">{r.result_summary}</span>
          )}
        </div>
      ),
    },
    {
      key: "usage",
      title: t("ai.colUsage"),
      width: 160,
      render: (_: unknown, r: AiGenerationItem) => (
        <div className="dm-set-usage">
          <span>
            {r.tokens_in} / {r.tokens_out}
          </span>
          <span className="dm-set-cell-muted">{r.duration_ms} ms</span>
        </div>
      ),
    },
    ...removeColumn,
  ];

  return (
    <div className="dm-set-page">
      <ResizableTable<AiGenerationItem>
        storageKey={storageKey}
        rowKey="id"
        size="middle"
        className="dm-set-table"
        loading={loading}
        dataSource={items}
        pagination={false}
        scroll={{ x: 1000 }}
        columns={columns}
      />
    </div>
  );
}

function StatCard({
  icon,
  label,
  value,
}: {
  icon: JSX.Element;
  label: string;
  value: number | string;
}) {
  return (
    <div className="dm-set-stat-card">
      <div className="dm-set-stat-card-icon">{icon}</div>
      <div className="dm-set-stat-card-text">
        <span className="dm-set-stat-card-label">{label}</span>
        <span className="dm-set-stat-card-value">{value}</span>
      </div>
    </div>
  );
}

export default function AiGenerationsTab() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [data, setData] = useState<AiGenerationsPage | null>(null);
  const [loading, setLoading] = useState(false);
  const [kind, setKind] = useState<string | undefined>();
  const [status, setStatus] = useState<string | undefined>();
  const [deploymentId, setDeploymentId] = useState<number | undefined>();
  const [page, setPage] = useState(1);
  const [view, setView] = useState<ViewMode>(() => {
    const v = localStorage.getItem(VIEW_KEY);
    return v === "card" || v === "table" ? v : "table";
  });

  const load = useCallback(
    (p = page) => {
      setLoading(true);
      AiGenApi.list({
        kind,
        status,
        deployment_id: deploymentId,
        page: p,
        page_size: 20,
      })
        .then(setData)
        .catch((e) => message.error((e as Error).message))
        .finally(() => setLoading(false));
    },
    [kind, status, deploymentId, page, message]
  );

  useEffect(() => {
    load(1);
    setPage(1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, status, deploymentId]);

  useEffect(() => {
    load(page);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page]);

  const persistView = (v: ViewMode) => {
    setView(v);
    localStorage.setItem(VIEW_KEY, v);
  };

  const cleanup = async (payload: {
    status?: string;
    older_than_days?: number;
    deployment_id?: number;
  }) => {
    try {
      const r = await AiGenApi.cleanup(payload);
      message.success(t("ai.cleanupDone", { count: r.deleted }));
      load(page);
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const stats = data?.stats;
  const items = data?.items || [];
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  const kindOptions = useMemo(
    () =>
      Object.keys(KIND_KEY).map((value) => ({
        value,
        label: t(KIND_KEY[value]),
      })),
    [t]
  );

  const renderCard = (r: AiGenerationItem) => {
    const tone = kindTone(r.kind);
    const ok = r.status === "ok";
    return (
      <div className="dm-set-card" key={r.id}>
        <div
          className="dm-set-card-accent"
          style={{ background: ok ? tone : "#ff4d4f" }}
        />
        <div className="dm-set-card-inner">
          <div className="dm-set-card-header">
            <div
              className="dm-set-card-avatar"
              style={{ color: tone, background: `${tone}14` }}
            >
              <ThunderboltOutlined />
            </div>
            <div className="dm-set-card-title-block">
              <div className="dm-set-card-name-row">
                <span className="dm-set-card-name">{t(kindLabel(r.kind))}</span>
                {ok ? (
                  <CheckCircleFilled style={{ color: "#52c41a" }} />
                ) : (
                  <CloseCircleFilled style={{ color: "#ff4d4f" }} />
                )}
              </div>
              <span className="dm-set-card-handle">{r.created_at}</span>
            </div>
          </div>

          <div className="dm-set-summary dm-set-summary-card">
            <span className="dm-set-summary-prompt">
              {r.prompt_summary || "—"}
            </span>
            {r.result_summary && (
              <span className="dm-set-summary-result">{r.result_summary}</span>
            )}
          </div>

          <div className="dm-set-card-meta">
            <span className="dm-set-pill">
              {r.tokens_in} / {r.tokens_out}
            </span>
            <span className="dm-set-pill">
              <ClockCircleOutlined />
              {r.duration_ms} ms
            </span>
            {r.deployment_name && (
              <span className="dm-set-pill">{r.deployment_name}</span>
            )}
          </div>

          <div className="dm-set-card-footer">
            <Popconfirm
              title={t("ai.deleteConfirm")}
              onConfirm={async () => {
                await AiGenApi.remove(r.id);
                load(page);
              }}
            >
              <button
                type="button"
                className="dm-set-icon-btn dm-set-icon-btn-danger"
                title={t("ai.colActions")}
              >
                <DeleteOutlined />
              </button>
            </Popconfirm>
            <span className="dm-set-card-footer-spacer" />
            <span className="dm-set-id-badge">#{r.id}</span>
          </div>
        </div>
      </div>
    );
  };

  return (
    <div className="dm-set-page">
      <div className="dm-set-page-top">
        <div className="dm-set-legend" role="note">
          <span className="dm-set-legend-label">
            {t("ai.legendTitle")}
          </span>
          <span className="dm-set-legend-text">{t("ai.legend")}</span>
        </div>
        <div className="dm-set-grid-toolbar">
          <div className="dm-set-filter-row">
            <Select
              allowClear
              placeholder={t("ai.allKind")}
              style={{ width: 150 }}
              value={kind}
              onChange={setKind}
              options={kindOptions}
            />
            <Select
              allowClear
              placeholder={t("ai.allDeployment")}
              style={{ width: 180 }}
              value={deploymentId}
              onChange={setDeploymentId}
              options={(data?.deployments || []).map((d) => ({
                value: d.id,
                label: d.name,
              }))}
            />
            <Select
              allowClear
              placeholder={t("ai.allStatus")}
              style={{ width: 120 }}
              value={status}
              onChange={setStatus}
              options={[
                { value: "ok", label: t("ai.statusOk") },
                { value: "error", label: t("ai.statusError") },
              ]}
            />
          </div>
          <div className="dm-set-grid-toolbar-right">
            <Segmented<ViewMode>
              size="small"
              value={view}
              onChange={persistView}
              options={[
                { value: "card", icon: <AppstoreOutlined /> },
                { value: "table", icon: <UnorderedListOutlined /> },
              ]}
            />
            <Button icon={<ReloadOutlined />} onClick={() => load(page)}>
              {t("ai.refresh")}
            </Button>
            <Popconfirm
              title={t("ai.cleanupFailedConfirm")}
              onConfirm={() =>
                cleanup({ status: "error", deployment_id: deploymentId })
              }
            >
              <Button>{t("ai.cleanupFailed")}</Button>
            </Popconfirm>
            <Popconfirm
              title={t("ai.cleanup30Confirm")}
              onConfirm={() =>
                cleanup({ older_than_days: 30, deployment_id: deploymentId })
              }
            >
              <Button>{t("ai.cleanup30")}</Button>
            </Popconfirm>
          </div>
        </div>
      </div>

      <div className="dm-set-stat-cards">
        <StatCard
          icon={<ThunderboltOutlined />}
          label={t("ai.statTotal")}
          value={stats?.total ?? 0}
        />
        <StatCard
          icon={<span className="dm-set-stat-arrow">↓</span>}
          label={t("ai.statIn")}
          value={stats?.tokens_in ?? 0}
        />
        <StatCard
          icon={<span className="dm-set-stat-arrow">↑</span>}
          label={t("ai.statOut")}
          value={stats?.tokens_out ?? 0}
        />
        <StatCard
          icon={<ClockCircleOutlined />}
          label={t("ai.statAvg")}
          value={stats?.avg_duration_ms ?? 0}
        />
      </div>

      {stats && Object.keys(stats.by_kind).length > 0 && (
        <div className="dm-set-filter-row dm-set-kind-row">
          {Object.entries(stats.by_kind).map(([k, n]) => (
            <span
              key={k}
              className="dm-set-pill"
              style={{ color: kindTone(k), background: `${kindTone(k)}14` }}
            >
              {t(kindLabel(k))} {n}
            </span>
          ))}
        </div>
      )}

      {view === "card" ? (
        loading ? (
          <div className="dm-set-grid-loading">
            <Spin />
          </div>
        ) : items.length === 0 ? (
          <Empty description={t("ai.noRecords")} />
        ) : (
          <div className="dm-set-card-grid">{items.map(renderCard)}</div>
        )
      ) : (
        <GenerationTable
          data={data}
          loading={loading}
          onRemove={async (item) => {
            try {
              await AiGenApi.remove(item.id);
              load(page);
            } catch (e) {
              message.error((e as Error).message);
            }
          }}
        />
      )}

      {data && data.total > data.page_size && (
        <div className="dm-set-pagination">
          <Button
            size="small"
            disabled={page <= 1}
            onClick={() => setPage(page - 1)}
          >
            {t("ai.prevPage")}
          </Button>
          <span className="dm-set-cell-muted">
            {t("ai.pageInfo", { page, pages })}
          </span>
          <Button
            size="small"
            disabled={page >= pages}
            onClick={() => setPage(page + 1)}
          >
            {t("ai.nextPage")}
          </Button>
        </div>
      )}
    </div>
  );
}
