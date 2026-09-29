import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { App, Button, Result, Space, Spin, Switch, Tooltip } from "antd";
import {
  ApiOutlined,
  AppstoreOutlined,
  ArrowLeftOutlined,
  CrownOutlined,
  FileSearchOutlined,
  LineChartOutlined,
  MoonOutlined,
  NodeIndexOutlined,
  PlayCircleOutlined,
  RobotOutlined,
  SunOutlined,
  TeamOutlined,
} from "@ant-design/icons";
import { DeployApi, WorkspaceApi, WorkspaceInfo, WorkspaceMenu, WorkspacePage } from "../../api";
import Sidebar, { type NavItem, type NavSection } from "../../layouts/Sidebar";
import RailEdgeControl from "../../components/RailEdgeControl";
import Header from "../../layouts/Header";
import { useIsMobile } from "../../hooks/useIsMobile";
import { useTheme } from "../../context/ThemeContext";
import PageRenderer from "./PageRenderer";
import MembersPage from "./MembersPage";
import LogsPage from "./LogsPage";
import EngineerMenusPage from "./EngineerMenusPage";
import EngineerFlowsPage from "./EngineerFlowsPage";
import ApiDocsPage from "./ApiDocsPage";
import AskData from "./AskData";
import DeploymentGenerationsPage from "./DeploymentGenerationsPage";
import { DemoContext } from "./demo";

const SIDEBAR_COLLAPSED_KEY = "datamason:workspace:sidebar:collapsed";

function getSavedCollapsed(): boolean {
  try {
    const saved = localStorage.getItem(SIDEBAR_COLLAPSED_KEY);
    if (saved !== null) return saved === "true";
  } catch {
    /* localStorage 不可用 */
  }
  return false;
}

interface Props {
  deploymentId: number;
}

export interface RecordFocus {
  table: string;
  pk: string;
  nonce: number;
}

type SystemPage = "menus" | "flows" | "members" | "logs" | "api" | "ai";

/** M2-2 工作台运行时：入口 path=/app/{deployment_id}，左菜单树 + 右页面内容区。
 *  外壳按 WorkMason 布局重做：Sidebar（分组导航 + 拖拽边）+ Header + page-content。
 *  M2-5：成员与权限；M2-6：操作日志；M3-2：界面与菜单；M3-4：流程编排；
 *  M3-3：演示展厅；M4-2：问数据。 */
export default function Workspace({ deploymentId }: Props) {
  const { message } = App.useApp();
  const navigate = useNavigate();
  const isMobile = useIsMobile();
  const { isDark, toggle } = useTheme();
  const [searchParams, setSearchParams] = useSearchParams();
  const [info, setInfo] = useState<WorkspaceInfo | null>(null);
  const [error, setError] = useState("");
  const [activePage, setActivePage] = useState<number | null>(null);
  const [systemPage, setSystemPage] = useState<SystemPage | null>(null);
  const [focus, setFocus] = useState<RecordFocus | null>(null);
  const [restarting, setRestarting] = useState(false);
  const [demo, setDemo] = useState(false);
  const [masked, setMasked] = useState(false);
  const [askOpen, setAskOpen] = useState(false);
  const [collapsed, setCollapsed] = useState<boolean>(() => getSavedCollapsed());
  const rootRef = useRef<HTMLDivElement>(null);

  const persistCollapsed = useCallback((next: boolean) => {
    setCollapsed(next);
    try {
      localStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(next));
    } catch {
      /* 忽略持久化失败 */
    }
  }, []);

  useEffect(() => {
    setCollapsed(isMobile ? true : getSavedCollapsed());
  }, [isMobile]);

  const loadInfo = useCallback(() => {
    setInfo(null);
    setError("");
    setActivePage(null);
    setSystemPage(null);
    setFocus(null);
    WorkspaceApi.info(deploymentId)
      .then(setInfo)
      .catch((e) => setError(e.message));
  }, [deploymentId]);

  useEffect(() => {
    loadInfo();
  }, [loadInfo]);

  // M4-3：钉住/取消钉住后只刷新菜单与页面，不清空当前视图（避免抽屉与选中页被重置）
  const refreshInfo = useCallback(() => {
    WorkspaceApi.info(deploymentId).then(setInfo).catch(() => {
      /* 忽略：保持现有视图 */
    });
  }, [deploymentId]);

  // 菜单点击 → 选页面；初始依 URL ?page=<id>，否则取第一棵子菜单指向的页面
  const firstPageId = useMemo(() => firstLeafRef(info?.menus) ?? info?.pages[0]?.id ?? null, [info]);

  useEffect(() => {
    if (!info || error || activePage !== null) return;
    const fromQuery = Number(searchParams.get("page") || 0);
    setActivePage(info.pages.some((p) => p.id === fromQuery) ? fromQuery : firstPageId);
  }, [info, error, activePage, firstPageId, searchParams]);

  const canSeeSystemPages = info && (info.role === "creator" || info.role === "admin");
  const canEditMembers = info?.role === "creator";

  // M3-3 演示模式：进入即隐藏构建痕迹（DR：只剩干净菜单+运行界面+数据）
  const exitDemo = useCallback(() => {
    setDemo(false);
    setMasked(false);
    if (document.fullscreenElement) {
      document.exitFullscreen().catch(() => {
        /* 忽略：非全屏或权限受限 */
      });
    }
  }, []);

  const enterDemo = useCallback(() => {
    setSystemPage(null);
    setFocus(null);
    if (activePage === null) setActivePage(firstPageId);
    setDemo(true);
    rootRef.current?.requestFullscreen?.().catch(() => {
      /* 浏览器不支持/被拒时仍进入演示（无全屏） */
    });
  }, [activePage, firstPageId]);

  // 离开演示仅限 Ctrl/Cmd+Shift+D（防演示中误触）
  useEffect(() => {
    if (!demo) return;
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.shiftKey && (e.key === "D" || e.key === "d")) {
        e.preventDefault();
        exitDemo();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [demo, exitDemo]);

  // 浏览器退出全屏（Esc 等）时同步退出演示，避免状态错位
  useEffect(() => {
    if (!demo) return;
    const onFs = () => {
      if (!document.fullscreenElement) {
        setDemo(false);
        setMasked(false);
      }
    };
    document.addEventListener("fullscreenchange", onFs);
    return () => document.removeEventListener("fullscreenchange", onFs);
  }, [demo]);

  const sections = useMemo<NavSection[]>(
    () => (info ? buildWorkspaceSections(info, demo) : []),
    [info, demo],
  );

  const selectedKey = systemPage
    ? `sys:${systemPage}`
    : activePage !== null
      ? `page:${activePage}`
      : "";

  const handleNav = (item: NavItem) => {
    if (isMobile) persistCollapsed(true);
    const key = item.key;
    if (key.startsWith("sys:")) {
      setFocus(null);
      setSystemPage(key.slice("sys:".length) as SystemPage);
      return;
    }
    if (key.startsWith("page:")) {
      const pageId = Number(key.slice("page:".length));
      if (Number.isInteger(pageId)) {
        setSystemPage(null);
        setActivePage(pageId);
        setSearchParams({ page: String(pageId) }, { replace: true });
      }
    }
  };

  const handleViewRecord = (table: string, pk: string) => {
    const target = info?.pages.find((p) => p.kind === "crud_list" && p.table === table);
    if (target) {
      setSystemPage(null);
      setActivePage(target.id);
      setSearchParams({ page: String(target.id) }, { replace: true });
      setFocus({ table, pk, nonce: Date.now() });
    }
  };

  const active: WorkspacePage | null =
    systemPage !== null ? null : (info?.pages.find((p) => p.id === activePage) ?? null);

  // M3-1：停止态入口 → 「系统已停止」提示页（数据保留；创建人可一键再启动）
  if (info?.status === "stopped") {
    return (
      <FullPage>
      <Result
        status="info"
        title="系统已停止"
        subTitle={
          info.role === "creator"
            ? `「${info.biz_name}」已停止，数据已保留；可在控制台或此处再启动恢复使用。`
            : `「${info.biz_name}」已由创建人停止，数据已保留；请联系创建人再启动。`
        }
        extra={
          <Space>
            <Button type="primary" icon={<ArrowLeftOutlined />} onClick={() => navigate("/deployments")}>
              返回控制台
            </Button>
            {info.role === "creator" && (
              <Button
                icon={<PlayCircleOutlined />}
                loading={restarting}
                onClick={async () => {
                  setRestarting(true);
                  try {
                    await DeployApi.start(deploymentId);
                    message.success("已再启动");
                    loadInfo();
                  } catch (e) {
                    message.error((e as Error).message);
                  } finally {
                    setRestarting(false);
                  }
                }}
              >
                再启动
              </Button>
            )}
          </Space>
        }
      />
      </FullPage>
    );
  }

  if (error) {
    return (
      <FullPage>
        <Result
          status="warning"
          title="无法进入工作台"
          subTitle={error}
          extra={
            <Button type="primary" icon={<ArrowLeftOutlined />} onClick={() => navigate("/deployments")}>
              返回控制台
            </Button>
          }
        />
      </FullPage>
    );
  }

  if (!info) {
    return (
      <FullPage>
        <Spin tip="正在进入工作台…" />
      </FullPage>
    );
  }

  const currentTitle =
    systemPage === "menus"
      ? "界面与菜单"
      : systemPage === "flows"
        ? "流程编排"
        : systemPage === "members"
          ? "成员与权限"
          : systemPage === "logs"
            ? "操作日志"
            : systemPage === "api"
              ? "API 文档"
              : systemPage === "ai"
                ? "AI 生成"
                : (active?.title || "工作台");

  const railCollapsed = collapsed && !isMobile;

  const brand = railCollapsed ? (
    <CrownOutlined style={{ fontSize: 18, color: "var(--fn-text-brand)" }} />
  ) : (
    <span style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
      <CrownOutlined style={{ color: "var(--fn-text-brand)" }} />
      <span
        style={{
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          fontWeight: 700,
          fontSize: 15,
          color: "var(--fn-text-primary)",
        }}
      >
        {info.biz_name}
      </span>
    </span>
  );

  const footer = (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: railCollapsed ? "center" : "space-between",
        gap: 4,
        padding: railCollapsed ? "10px 0" : "10px 12px",
      }}
    >
      <button type="button" className="dm-sidebar__icon-btn" onClick={toggle} aria-label="切换主题">
        {isDark ? <SunOutlined /> : <MoonOutlined />}
      </button>
      {railCollapsed ? (
        <Tooltip title="返回控制台" placement="right">
          <button
            type="button"
            className="dm-sidebar__icon-btn"
            onClick={() => navigate("/deployments")}
            aria-label="返回控制台"
          >
            <ArrowLeftOutlined />
          </button>
        </Tooltip>
      ) : (
        <Button size="small" type="text" icon={<ArrowLeftOutlined />} onClick={() => navigate("/deployments")}>
          返回控制台
        </Button>
      )}
    </div>
  );

  const actions = (
    <Space size={8}>
      {canSeeSystemPages && !demo && (
        <Button size="small" icon={<LineChartOutlined />} onClick={() => setAskOpen(true)}>
          问数据
        </Button>
      )}
      {!demo && (
        <Button size="small" icon={<PlayCircleOutlined />} onClick={enterDemo}>
          演示
        </Button>
      )}
    </Space>
  );

  return (
    <DemoContext.Provider value={{ demo, masked }}>
      <div
        ref={rootRef}
        style={{
          height: "100dvh",
          width: "100%",
          display: "flex",
          flexDirection: "row",
          background: "var(--fn-bg-layout)",
          overflow: "hidden",
        }}
      >
        {isMobile && !collapsed && (
          <div
            onClick={() => persistCollapsed(true)}
            style={{ position: "fixed", inset: 0, background: "var(--fn-overlay)", zIndex: 99 }}
          />
        )}

        <div style={{ position: "relative", flexShrink: 0, alignSelf: "stretch", display: "flex", minHeight: 0 }}>
          <Sidebar
            sections={sections}
            selectedKey={selectedKey}
            collapsed={collapsed}
            onToggle={() => persistCollapsed(!collapsed)}
            onNavigate={handleNav}
            brand={brand}
            footer={footer}
            isMobile={isMobile}
          />
          {!isMobile && (
            <RailEdgeControl expanded={!collapsed} onToggle={() => persistCollapsed(!collapsed)} side="end" />
          )}
        </div>

        <div
          style={{
            flex: 1,
            minWidth: 0,
            minHeight: 0,
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
          }}
        >
          {isMobile && !demo ? (
            <Header onToggle={() => persistCollapsed(!collapsed)} extra={actions} />
          ) : (
            !demo && (
              <div
                style={{
                  height: 48,
                  flexShrink: 0,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  padding: "0 24px",
                  background: "var(--fn-bg-primary)",
                  borderBottom: "1px solid var(--fn-border-primary)",
                }}
              >
                <span style={{ fontSize: 14, fontWeight: 600, color: "var(--fn-text-primary)" }}>
                  {currentTitle}
                </span>
                {actions}
              </div>
            )
          )}
          <div className="page-content" style={{ flex: 1, padding: 24 }}>
            {systemPage === "menus" && info?.role === "creator" ? (
              <EngineerMenusPage deploymentId={deploymentId} />
            ) : systemPage === "flows" && info?.role === "creator" ? (
              <EngineerFlowsPage deploymentId={deploymentId} />
            ) : systemPage === "members" && canSeeSystemPages ? (
              <MembersPage deploymentId={deploymentId} canEdit={canEditMembers} />
            ) : systemPage === "logs" && canSeeSystemPages ? (
              <LogsPage deploymentId={deploymentId} onViewRecord={handleViewRecord} />
            ) : systemPage === "api" && canSeeSystemPages ? (
              <ApiDocsPage deploymentId={deploymentId} canManageKeys={info?.role === "creator"} />
            ) : systemPage === "ai" && canSeeSystemPages ? (
              <DeploymentGenerationsPage deploymentId={deploymentId} />
            ) : (
              <PageRenderer
                deploymentId={deploymentId}
                page={active}
                role={info?.role}
                dataMode={info?.data_mode}
                focus={focus}
              />
            )}
          </div>
        </div>

        {demo && (
          <div
            style={{
              position: "fixed",
              top: 12,
              right: 16,
              zIndex: 1000,
              display: "flex",
              alignItems: "center",
              gap: 10,
              background: "var(--fn-bg-elevated)",
              border: "1px solid var(--fn-border-primary)",
              borderRadius: 999,
              padding: "6px 14px",
              boxShadow: "var(--fn-shadow-md)",
            }}
          >
            <Switch
              size="small"
              checked={masked}
              onChange={setMasked}
              checkedChildren="脱敏"
              unCheckedChildren="原数据"
            />
            <span style={{ fontSize: 12, color: "var(--fn-text-secondary)" }}>
              Ctrl/Cmd+Shift+D 退出演示
            </span>
          </div>
        )}
        <AskData
          deploymentId={deploymentId}
          open={askOpen}
          onClose={() => setAskOpen(false)}
          canPin={!!canSeeSystemPages}
          onPinned={refreshInfo}
        />
      </div>
    </DemoContext.Provider>
  );
}

/** 全屏占位容器：承载 Result/Spin 等独立状态页，背景跟随当前主题。 */
function FullPage({ children }: { children: ReactNode }) {
  return (
    <div
      style={{
        height: "100dvh",
        width: "100%",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "var(--fn-bg-layout)",
      }}
    >
      {children}
    </div>
  );
}

/** 取第一个有 ref_id 的叶子菜单指向的页面；没有则 null。 */
function firstLeafRef(root: WorkspaceMenu[] | undefined): number | null {
  if (!root) return null;
  for (const m of root) {
    if (m.children.length === 0) {
      if (m.ref_id) return m.ref_id;
    } else {
      const child = firstLeafRef(m.children);
      if (child !== null) return child;
    }
  }
  return null;
}

/** 业务菜单叶子（递归展平）→ 侧栏导航项。 */
function menuLeafItems(menus: WorkspaceMenu[]): NavItem[] {
  const out: NavItem[] = [];
  for (const m of menus) {
    if (m.children.length === 0) {
      if (m.ref_id) out.push({ key: `page:${m.ref_id}`, label: m.label });
    } else {
      out.push(...menuLeafItems(m.children));
    }
  }
  return out;
}

/** 业务菜单 + 系统页 → WorkMason 式分组侧栏（一级带子菜单的作为分组）。 */
function buildWorkspaceSections(info: WorkspaceInfo, demo: boolean): NavSection[] {
  const sections: NavSection[] = [];
  let flat: NavItem[] = [];
  const flush = () => {
    if (flat.length) {
      sections.push({ items: flat });
      flat = [];
    }
  };
  for (const m of info.menus) {
    if (m.children.length === 0) {
      if (m.ref_id) flat.push({ key: `page:${m.ref_id}`, label: m.label });
    } else {
      flush();
      sections.push({ groupKey: m.label, items: menuLeafItems(m.children) });
    }
  }
  flush();

  if (demo || (info.role !== "creator" && info.role !== "admin")) return sections;

  if (info.role === "creator") {
    sections.push({
      groupKey: "工程室",
      items: [
        { key: "sys:menus", label: "界面与菜单", icon: <AppstoreOutlined /> },
        { key: "sys:flows", label: "流程编排", icon: <NodeIndexOutlined /> },
      ],
    });
  }
  sections.push({
    groupKey: "管理",
    items: [
      { key: "sys:members", label: "成员与权限", icon: <TeamOutlined /> },
      { key: "sys:logs", label: "操作日志", icon: <FileSearchOutlined /> },
      { key: "sys:api", label: "API 文档", icon: <ApiOutlined /> },
      { key: "sys:ai", label: "AI 生成", icon: <RobotOutlined /> },
    ],
  });
  return sections;
}
