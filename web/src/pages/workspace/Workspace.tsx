import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { App, Button, Layout, Menu, Result, Space, Spin, Switch, Typography } from "antd";
import type { MenuProps } from "antd";
import {
  AppstoreOutlined,
  ArrowLeftOutlined,
  CrownOutlined,
  FileSearchOutlined,
  NodeIndexOutlined,
  PlayCircleOutlined,
  TeamOutlined,
} from "@ant-design/icons";
import { DeployApi, WorkspaceApi, WorkspaceInfo, WorkspaceMenu, WorkspacePage } from "../../api";
import PageRenderer from "./PageRenderer";
import MembersPage from "./MembersPage";
import LogsPage from "./LogsPage";
import EngineerMenusPage from "./EngineerMenusPage";
import EngineerFlowsPage from "./EngineerFlowsPage";
import { DemoContext } from "./demo";

const { Header, Sider, Content } = Layout;
const { Text } = Typography;

interface Props {
  deploymentId: number;
}

export interface RecordFocus {
  table: string;
  pk: string;
  nonce: number;
}

/** M2-2 工作台运行时：入口 path=/app/{deployment_id}，左菜单树 + 右页面内容区。
 *  M2-5：新增「成员与权限」系统页（创建人见可编辑，管理员见只读，操作员不见）。
 *  M2-6：新增「操作日志」系统页（创建人/管理员常驻）；日志→记录双向跳转。
 *  M3-2：新增「界面与菜单」系统页（创建人独有 DR-14）——真实界面卡拖入导航树即拖即存。
 *  M3-4：新增「流程编排」系统页（创建人独有）。
 *  M3-3：顶栏「演示」一键进入展厅——全屏、隐藏构建痕迹（系统页/返回控制台/运行提示），
 *        只剩干净业务菜单 + 运行界面 + 数据；Ctrl/Cmd+Shift+D 退出；含「脱敏数据」展示层开关（不动库）。 */
export default function Workspace({ deploymentId }: Props) {
  const { message } = App.useApp();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [info, setInfo] = useState<WorkspaceInfo | null>(null);
  const [error, setError] = useState("");
  const [activePage, setActivePage] = useState<number | null>(null);
  const [systemPage, setSystemPage] = useState<"menus" | "flows" | "members" | "logs" | null>(null);
  const [focus, setFocus] = useState<RecordFocus | null>(null);
  const [restarting, setRestarting] = useState(false);
  const [demo, setDemo] = useState(false);
  const [masked, setMasked] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

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

  const menuItems = useMemo<MenuProps["items"]>(() => {
    const base = toMenuItems(info?.menus || []);
    if (demo || !canSeeSystemPages) return base;
    return [
      ...(base || []),
      { type: "divider" as const },
      ...(info?.role === "creator"
        ? [
            {
              key: "sys:menus",
              icon: <AppstoreOutlined />,
              label: "界面与菜单",
            },
            {
              key: "sys:flows",
              icon: <NodeIndexOutlined />,
              label: "流程编排",
            },
          ]
        : []),
      {
        key: "sys:members",
        icon: <TeamOutlined />,
        label: "成员与权限",
      },
      {
        key: "sys:logs",
        icon: <FileSearchOutlined />,
        label: "操作日志",
      },
    ];
  }, [info, canSeeSystemPages, demo]);

  const onClick: MenuProps["onClick"] = ({ key }) => {
    if (key === "sys:menus" || key === "sys:flows" || key === "sys:members" || key === "sys:logs") {
      setFocus(null);
      setSystemPage(
        key === "sys:menus"
          ? "menus"
          : key === "sys:flows"
            ? "flows"
            : key === "sys:members"
              ? "members"
              : "logs",
      );
      return;
    }
    if (!key.startsWith("page:")) return;
    setSystemPage(null);
    const pageId = Number(key.slice("page:".length));
    if (Number.isInteger(pageId)) {
      setActivePage(pageId);
      setSearchParams({ page: String(pageId) }, { replace: true });
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
    );
  }

  if (error) {
    return (
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
    );
  }

  if (!info) {
    return (
      <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center" }}>
        <Spin tip="正在进入工作台…" />
      </div>
    );
  }

  return (
    <DemoContext.Provider value={{ demo, masked }}>
      <div ref={rootRef} style={{ minHeight: "100vh" }}>
        <Layout style={{ minHeight: "100vh" }}>
          <Sider theme="dark" width={220}>
            <div
              style={{
                height: 56,
                display: "flex",
                alignItems: "center",
                gap: 8,
                padding: "0 16px",
                color: "#fff",
                fontWeight: 700,
                fontSize: 15,
              }}
            >
              <CrownOutlined />
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {info.biz_name}
              </span>
            </div>
            <Menu
              theme="dark"
              mode="inline"
              items={menuItems}
              selectedKeys={
                systemPage === "menus"
                  ? ["sys:menus"]
                  : systemPage === "flows"
                    ? ["sys:flows"]
                    : systemPage === "members"
                      ? ["sys:members"]
                      : systemPage === "logs"
                        ? ["sys:logs"]
                        : activePage !== null
                          ? [`page:${activePage}`]
                          : []
              }
              onClick={onClick}
            />
          </Sider>
          <Layout>
            {!demo && (
              <Header
                style={{
                  background: "#fff",
                  paddingInline: 24,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  borderBottom: "1px solid #f0f0f0",
                }}
              >
                <Text strong>
                  {systemPage === "menus"
                    ? "界面与菜单"
                    : systemPage === "flows"
                      ? "流程编排"
                      : systemPage === "members"
                        ? "成员与权限"
                        : systemPage === "logs"
                          ? "操作日志"
                          : (active?.title || "工作台")}
                </Text>
                <Space>
                  <Button size="small" icon={<PlayCircleOutlined />} onClick={enterDemo}>
                    演示
                  </Button>
                  <Button size="small" icon={<ArrowLeftOutlined />} onClick={() => navigate("/deployments")}>
                    返回控制台
                  </Button>
                </Space>
              </Header>
            )}
            <Content style={{ padding: 24 }}>
              {systemPage === "menus" && info?.role === "creator" ? (
                <EngineerMenusPage deploymentId={deploymentId} />
              ) : systemPage === "flows" && info?.role === "creator" ? (
                <EngineerFlowsPage deploymentId={deploymentId} />
              ) : systemPage === "members" && canSeeSystemPages ? (
                <MembersPage deploymentId={deploymentId} canEdit={canEditMembers} />
              ) : systemPage === "logs" && canSeeSystemPages ? (
                <LogsPage deploymentId={deploymentId} onViewRecord={handleViewRecord} />
              ) : (
                <PageRenderer
                  deploymentId={deploymentId}
                  page={active}
                  role={info?.role}
                  dataMode={info?.data_mode}
                  focus={focus}
                />
              )}
            </Content>
          </Layout>
        </Layout>
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
              background: "rgba(255,255,255,0.94)",
              border: "1px solid #f0f0f0",
              borderRadius: 999,
              padding: "6px 14px",
              boxShadow: "0 2px 10px rgba(0,0,0,0.12)",
            }}
          >
            <Switch
              size="small"
              checked={masked}
              onChange={setMasked}
              checkedChildren="脱敏"
              unCheckedChildren="原数据"
            />
            <Text type="secondary" style={{ fontSize: 12 }}>
              Ctrl/Cmd+Shift+D 退出演示
            </Text>
          </div>
        )}
      </div>
    </DemoContext.Provider>
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

function toMenuItems(menus: WorkspaceMenu[]): MenuProps["items"] {
  return menus.map((m) => {
    const leaf = m.children.length === 0;
    const key = leaf && m.ref_id ? `page:${m.ref_id}` : `group:${m.id}`;
    return {
      key,
      label: m.label,
      children: leaf ? undefined : toMenuItems(m.children),
    };
  });
}