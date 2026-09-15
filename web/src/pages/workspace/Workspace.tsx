import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Button, Layout, Menu, Result, Spin, Typography } from "antd";
import type { MenuProps } from "antd";
import { ArrowLeftOutlined, CrownOutlined } from "@ant-design/icons";
import { WorkspaceApi, WorkspaceInfo, WorkspaceMenu, WorkspacePage } from "../../api";
import PageRenderer from "./PageRenderer";

const { Header, Sider, Content } = Layout;
const { Text } = Typography;

interface Props {
  deploymentId: number;
}

/** M2-2 工作台运行时：入口 path=/app/{deployment_id}，左菜单树 + 右页面内容区。 */
export default function Workspace({ deploymentId }: Props) {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [info, setInfo] = useState<WorkspaceInfo | null>(null);
  const [error, setError] = useState("");
  const [activePage, setActivePage] = useState<number | null>(null);

  useEffect(() => {
    setInfo(null);
    setError("");
    setActivePage(null);
    WorkspaceApi.info(deploymentId)
      .then(setInfo)
      .catch((e) => setError(e.message));
  }, [deploymentId]);

  // 菜单点击 → 选页面；初始依 URL ?page=<id>，否则取第一棵子菜单指向的页面
  const firstPageId = useMemo(() => firstLeafRef(info?.menus) ?? info?.pages[0]?.id ?? null, [info]);

  useEffect(() => {
    if (!info || error || activePage !== null) return;
    const fromQuery = Number(searchParams.get("page") || 0);
    setActivePage(info.pages.some((p) => p.id === fromQuery) ? fromQuery : firstPageId);
  }, [info, error, activePage, firstPageId, searchParams]);

  const menuItems = useMemo<MenuProps["items"]>(() => toMenuItems(info?.menus || []), [info]);

  const onClick: MenuProps["onClick"] = ({ key }) => {
    if (!key.startsWith("page:")) return;
    const pageId = Number(key.slice("page:".length));
    if (Number.isInteger(pageId)) {
      setActivePage(pageId);
      setSearchParams({ page: String(pageId) }, { replace: true });
    }
  };

  const active: WorkspacePage | null =
    info?.pages.find((p) => p.id === activePage) ?? null;

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
          selectedKeys={activePage !== null ? [`page:${activePage}`] : []}
          onClick={onClick}
        />
      </Sider>
      <Layout>
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
          <Text strong>{active?.title || "工作台"}</Text>
          <Button size="small" icon={<ArrowLeftOutlined />} onClick={() => navigate("/deployments")}>
            返回控制台
          </Button>
        </Header>
        <Content style={{ padding: 24 }}>
          <PageRenderer deploymentId={deploymentId} page={active} />
        </Content>
      </Layout>
    </Layout>
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