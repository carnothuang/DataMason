import { useCallback, useEffect, useMemo, useState } from "react";
import {
  App,
  Button,
  Empty,
  Input,
  Segmented,
  Spin,
  Tooltip,
} from "antd";
import {
  AppstoreOutlined,
  CopyOutlined,
  DeleteOutlined,
  DownloadOutlined,
  EyeOutlined,
  PlayCircleOutlined,
  ProjectOutlined,
  ReloadOutlined,
  SearchOutlined,
  ShareAltOutlined,
  TableOutlined,
  ThunderboltOutlined,
  UnorderedListOutlined,
} from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import dayjs from "dayjs";
import { DeployApi, Deployment, downloadJson } from "../api";
import { ResizableTable } from "../components/ResizableTable";
import PageShell from "../layouts/PageShell";
import DataViewer from "./deploy/DataViewer";
import BlueprintPage from "./deploy/BlueprintPage";
import ShareModal from "./ShareModal";
import DestroyModal from "./DestroyModal";
import "./settings/shared.css";

const VIEW_KEY = "datamason:deployments:view";
const TABLE_WIDTH_KEY = "datamason:deployments:table-widths";
type ViewMode = "card" | "table";

const STATUS_DOT: Record<string, string> = {
  running: "#52c41a",
  deploying: "#1677ff",
  stopped: "#bfbfbf",
  draft: "#bfbfbf",
  destroyed: "#ff4d4f",
};

export default function Deployments() {
  const { t } = useTranslation();
  const { message, modal } = App.useApp();
  const navigate = useNavigate();
  const [rows, setRows] = useState<Deployment[]>([]);
  const [loading, setLoading] = useState(true);
  const [keyword, setKeyword] = useState("");
  const [openId, setOpenId] = useState<number | null>(null);
  const [blueprintId, setBlueprintId] = useState<number | null>(null);
  const [shareDep, setShareDep] = useState<Deployment | null>(null);
  const [destroyDep, setDestroyDep] = useState<Deployment | null>(null);
  const [exportingId, setExportingId] = useState<number | null>(null);
  const [view, setView] = useState<ViewMode>(() => {
    try {
      const saved = localStorage.getItem(VIEW_KEY);
      if (saved === "card" || saved === "table") return saved;
    } catch {
      /* localStorage 不可用时使用默认值 */
    }
    return "table";
  });
  const [isMobile, setIsMobile] = useState(
    () => typeof window !== "undefined" && window.innerWidth <= 768,
  );

  useEffect(() => {
    const onResize = () => setIsMobile(window.innerWidth <= 768);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRows(await DeployApi.list());
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => {
    void load();
  }, [load]);

  const persistView = (next: ViewMode) => {
    setView(next);
    try {
      localStorage.setItem(VIEW_KEY, next);
    } catch {
      /* 忽略持久化失败 */
    }
  };

  const filtered = useMemo(() => {
    const kw = keyword.trim().toLowerCase();
    if (!kw) return rows;
    return rows.filter(
      (d) =>
        d.biz_name.toLowerCase().includes(kw) ||
        d.purpose.toLowerCase().includes(kw) ||
        d.entry_path.toLowerCase().includes(kw),
    );
  }, [rows, keyword]);

  const onStop = (d: Deployment) => {
    modal.confirm({
      title: t("deployments.stopTitle", { name: d.biz_name }),
      content: t("deployments.stopContent"),
      okText: t("deployments.stopOk"),
      okButtonProps: { danger: true },
      cancelText: t("deployments.cancel"),
      onOk: async () => {
        try {
          await DeployApi.stop(d.id);
          message.success(t("deployments.stopDone"));
          void load();
        } catch (e) {
          message.error((e as Error).message);
        }
      },
    });
  };

  const onStart = async (d: Deployment) => {
    try {
      await DeployApi.start(d.id);
      message.success(t("deployments.startDone"));
      void load();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const onCopyEntry = (d: Deployment) => {
    const url = `${window.location.origin}${d.entry_path}`;
    void navigator.clipboard
      .writeText(url)
      .then(() => message.success(t("deployments.copyEntryDone")))
      .catch(() => message.error(t("deployments.copyEntryFail")));
  };

  const onExport = async (d: Deployment) => {
    setExportingId(d.id);
    try {
      const pkg = await DeployApi.exportPackage(d.id);
      downloadJson(`datamason-deploy-${d.id}.json`, pkg);
      message.success(t("deployments.exportDone"));
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setExportingId(null);
    }
  };

  const onClearSeed = (d: Deployment) => {
    modal.confirm({
      title: t("deployments.clearSeedTitle", { name: d.biz_name }),
      content: t("deployments.clearSeedContent"),
      okText: t("deployments.clearSeedOk"),
      okButtonProps: { danger: true },
      cancelText: t("deployments.cancel"),
      onOk: async () => {
        try {
          const r = await DeployApi.clearSeed(d.id);
          message.success(t("deployments.clearSeedDone", { count: r.deleted }));
          void load();
        } catch (e) {
          message.error((e as Error).message);
        }
      },
    });
  };

  const current = rows.find((r) => r.id === openId);
  const blueprint = rows.find((r) => r.id === blueprintId);
  if (blueprint) {
    return (
      <BlueprintPage
        deploymentId={blueprint.id}
        bizName={blueprint.biz_name}
        dataMode={blueprint.data_mode}
        onBack={() => setBlueprintId(null)}
      />
    );
  }
  if (current) {
    return (
      <DataViewer
        deploymentId={current.id}
        bizName={current.biz_name}
        dataMode={current.data_mode}
        onBack={() => setOpenId(null)}
        onOpenBlueprint={() => setBlueprintId(current.id)}
      />
    );
  }

  const actionButtons = (d: Deployment) => (
    <>
      {d.status === "running" && d.entry_path && (
        <Tooltip title={t("deployments.openWorkspace")}>
          <button
            type="button"
            className="dm-set-icon-btn"
            onClick={() => navigate(d.entry_path)}
            aria-label={t("deployments.openWorkspace")}
          >
            <ThunderboltOutlined />
          </button>
        </Tooltip>
      )}
      <Tooltip title={t("deployments.openData")}>
        <button
          type="button"
          className="dm-set-icon-btn"
          onClick={() => setOpenId(d.id)}
          aria-label={t("deployments.openData")}
        >
          <EyeOutlined />
        </button>
      </Tooltip>
      <Tooltip title={t("deployments.blueprint")}>
        <button
          type="button"
          className="dm-set-icon-btn"
          onClick={() => setBlueprintId(d.id)}
          aria-label={t("deployments.blueprint")}
        >
          <ProjectOutlined />
        </button>
      </Tooltip>
      {d.entry_path && (
        <Tooltip title={t("deployments.copyEntry")}>
          <button
            type="button"
            className="dm-set-icon-btn"
            onClick={() => onCopyEntry(d)}
            aria-label={t("deployments.copyEntry")}
          >
            <CopyOutlined />
          </button>
        </Tooltip>
      )}
      {d.role === "creator" && (
        <Tooltip title={t("deployments.share")}>
          <button
            type="button"
            className="dm-set-icon-btn"
            onClick={() => setShareDep(d)}
            aria-label={t("deployments.share")}
          >
            <ShareAltOutlined />
          </button>
        </Tooltip>
      )}
      {(d.role === "creator" || d.role === "admin") && (
        <Tooltip title={t("deployments.export")}>
          <button
            type="button"
            className="dm-set-icon-btn"
            disabled={exportingId === d.id}
            onClick={() => void onExport(d)}
            aria-label={t("deployments.export")}
          >
            {exportingId === d.id ? <Spin size="small" /> : <DownloadOutlined />}
          </button>
        </Tooltip>
      )}
      {d.seed_data && (d.role === "creator" || d.role === "admin") && (
        <Tooltip title={t("deployments.clearSeed")}>
          <button
            type="button"
            className="dm-set-icon-btn"
            onClick={() => onClearSeed(d)}
            aria-label={t("deployments.clearSeed")}
          >
            <TableOutlined />
          </button>
        </Tooltip>
      )}
      {d.status === "running" && d.role === "creator" && (
        <Tooltip title={t("deployments.stop")}>
          <button
            type="button"
            className="dm-set-icon-btn dm-set-icon-btn-danger"
            onClick={() => onStop(d)}
            aria-label={t("deployments.stop")}
          >
            <PlayCircleOutlined />
          </button>
        </Tooltip>
      )}
      {d.status === "stopped" && d.role === "creator" && (
        <Tooltip title={t("deployments.restart")}>
          <button
            type="button"
            className="dm-set-icon-btn"
            onClick={() => void onStart(d)}
            aria-label={t("deployments.restart")}
          >
            <PlayCircleOutlined />
          </button>
        </Tooltip>
      )}
      {d.role === "creator" && (
        <Tooltip title={t("deployments.destroy")}>
          <button
            type="button"
            className="dm-set-icon-btn dm-set-icon-btn-danger"
            onClick={() => setDestroyDep(d)}
            aria-label={t("deployments.destroy")}
          >
            <DeleteOutlined />
          </button>
        </Tooltip>
      )}
    </>
  );

  const columns = [
    {
      title: t("deployments.colName"),
      key: "biz_name",
      width: 240,
      fixed: isMobile ? undefined : ("left" as const),
      render: (_: unknown, d: Deployment) => (
        <div className="dm-set-cell">
          <span className="dm-set-cell-avatar">
            <ThunderboltOutlined />
          </span>
          <span className="dm-set-cell-text">
            <span className="dm-set-cell-name">
              {d.biz_name}
              {d.data_mode === "link" && (
                <span className="dm-set-cell-tag">{t("deployments.linkTag")}</span>
              )}
            </span>
            <span className="dm-set-cell-handle">
              {d.entry_path || t("deployments.noEntry")}
            </span>
          </span>
        </div>
      ),
    },
    {
      title: t("deployments.colStatus"),
      key: "status",
      width: 120,
      render: (_: unknown, d: Deployment) => (
        <span className="dm-set-pill">
          <span
            className="dm-set-status-dot"
            style={{ background: STATUS_DOT[d.status] || "#bfbfbf" }}
          />
          {t(`deployments.status_${d.status}`, d.status)}
        </span>
      ),
    },
    {
      title: t("deployments.colPurpose"),
      key: "purpose",
      dataIndex: "purpose",
      ellipsis: true,
      render: (v: string) => (
        <span className="dm-set-cell-muted">{v || "—"}</span>
      ),
    },
    {
      title: t("deployments.colCreatedAt"),
      key: "created_at",
      dataIndex: "created_at",
      width: 160,
      render: (v: string) => (
        <span className="dm-set-cell-muted">
          {v ? dayjs(v).format("YYYY/MM/DD HH:mm") : "—"}
        </span>
      ),
    },
    {
      title: t("deployments.colActions"),
      key: "actions",
      width: 260,
      fixed: isMobile ? undefined : ("right" as const),
      render: (_: unknown, d: Deployment) => actionButtons(d),
    },
  ];

  const renderCard = (d: Deployment) => (
    <div
      key={d.id}
      className={`dm-set-card${
        d.status === "stopped" || d.status === "destroyed"
          ? " dm-set-card-muted"
          : ""
      }`}
    >
      <div
        className={`dm-set-card-accent ${
          d.status === "destroyed"
            ? "dm-set-card-accent-danger"
            : d.status === "running"
              ? "dm-set-card-accent-brand"
              : "dm-set-card-accent-neutral"
        }`}
      />
      <div className="dm-set-card-inner">
        <div className="dm-set-card-header">
          <div className="dm-set-card-avatar" aria-hidden="true">
            <ThunderboltOutlined />
          </div>
          <div className="dm-set-card-title-block">
            <div className="dm-set-card-name-row">
              <span className="dm-set-card-name">{d.biz_name}</span>
              {d.data_mode === "link" && (
                <span className="dm-set-card-tag">{t("deployments.linkTag")}</span>
              )}
            </div>
            <div className="dm-set-card-handle">
              {d.entry_path || t("deployments.noEntry")}
            </div>
          </div>
        </div>

        <div className="dm-set-card-meta">
          <span className="dm-set-pill">
            <span
              className="dm-set-status-dot"
              style={{ background: STATUS_DOT[d.status] || "#bfbfbf" }}
            />
            {t(`deployments.status_${d.status}`, d.status)}
          </span>
          {d.seed_data && (
            <span className="dm-set-pill">{t("deployments.seedTag")}</span>
          )}
        </div>

        <div className="dm-set-card-info">
          <span className="dm-set-card-time">
            <ProjectOutlined />
            <span
              style={{
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}
            >
              {d.purpose || "—"}
            </span>
          </span>
          <span className="dm-set-card-time">
            {d.created_at ? dayjs(d.created_at).format("YYYY/MM/DD HH:mm") : "—"}
          </span>
        </div>

        <div className="dm-set-card-footer">
          {actionButtons(d)}
          <span className="dm-set-card-footer-spacer" />
          <span className="dm-set-id-badge">#{d.id}</span>
        </div>
      </div>
    </div>
  );

  return (
    <>
      <PageShell
        title={t("deployments.title")}
        subtitle={t("deployments.subtitle")}
      >
        <div className="dm-set-page">
          <div className="dm-set-page-top">
            <div className="dm-set-legend" role="note">
              <span className="dm-set-legend-label">
                {t("deployments.legendTitle")}
              </span>
              <p className="dm-set-legend-text">{t("deployments.legend")}</p>
            </div>
          <div className="dm-set-grid-toolbar">
            <Input
              allowClear
              prefix={<SearchOutlined />}
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              placeholder={t("deployments.searchPlaceholder")}
              className="dm-set-search"
            />
            <div className="dm-set-grid-toolbar-right">
              <Segmented
                size="small"
                value={view}
                onChange={(v) => persistView(v as ViewMode)}
                options={[
                  {
                    value: "card",
                    label: (
                      <span className="dm-set-view-mode-label">
                        <AppstoreOutlined />
                        {t("deployments.viewCard")}
                      </span>
                    ),
                  },
                  {
                    value: "table",
                    label: (
                      <span className="dm-set-view-mode-label">
                        <UnorderedListOutlined />
                        {t("deployments.viewTable")}
                      </span>
                    ),
                  },
                ]}
              />
              <Button icon={<ReloadOutlined />} onClick={() => void load()}>
                {t("deployments.refresh")}
              </Button>
            </div>
          </div>
        </div>

        {view === "card" ? (
          loading && rows.length === 0 ? (
            <div className="dm-set-grid-loading">
              <Spin />
            </div>
          ) : filtered.length === 0 ? (
            <Empty
              description={
                rows.length === 0
                  ? t("deployments.noDeployments")
                  : t("deployments.noMatch")
              }
            />
          ) : (
            <div className="dm-set-card-grid">{filtered.map(renderCard)}</div>
          )
        ) : (
          <ResizableTable
            storageKey={TABLE_WIDTH_KEY}
            rowKey="id"
            size="middle"
            className="dm-set-table"
            loading={loading}
            dataSource={filtered}
            pagination={false}
            scroll={{ x: 1080 }}
            columns={columns}
          />
        )}
        </div>
      </PageShell>

      <ShareModal
        deploymentId={shareDep?.id ?? null}
        bizName={shareDep?.biz_name ?? ""}
        open={shareDep !== null}
        onClose={() => setShareDep(null)}
      />

      <DestroyModal
        deployment={destroyDep}
        open={destroyDep !== null}
        onClose={() => setDestroyDep(null)}
        onDestroyed={load}
      />
    </>
  );
}
