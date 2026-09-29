import { useEffect, useState } from "react";
import { Card, Empty, Spin, Typography } from "antd";
import {
  LayoutApi,
  RuntimePageLayout,
  WorkspacePage,
} from "../../api";
import CrudListPage from "./CrudListPage";
import FlowWizardPage from "./FlowWizardPage";
import DashboardPage from "./DashboardPage";
import PublicPage from "../PublicPage";
import PageLayoutRenderer from "../../components/PageLayoutRenderer";
import type { RecordFocus } from "./Workspace";

const { Text } = Typography;

interface Props {
  deploymentId: number;
  page: WorkspacePage | null;
  role?: string;
  dataMode?: string;
  focus?: RecordFocus | null;
}

/** 按 page_spec 渲染内容区：crud_list（M2-4）/ flow（M3-4）/ dashboard（M4-3）/ public_form（M3-5）。
 *  M2-5：role=operator 时列表受「我的记录」过滤（服务端强制 created_by=自己，前端仅提示）。
 *  M2-6：日志→记录「查看这条记录」聚焦跳转（focus 命中本页表时传给 CRUD 列表定位行）。
 *  M2-7：直连模式缺归属列表在 CRUD 列表内降级明示。
 *  M9-8：crud_list/dashboard 页若配置了页面布局则走 PageLayoutRenderer，未配置（"{}"）回退默认。 */
export default function PageRenderer({ deploymentId, page, role, dataMode, focus }: Props) {
  if (!page) {
    return (
      <Card>
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="该部署还没有可渲染的页面（菜单可能指向了尚未实现的页面类型）"
        />
      </Card>
    );
  }

  if (page.kind === "crud_list" && page.table) {
    return (
      <LayoutGate
        deploymentId={deploymentId}
        pageId={page.id}
        fallback={
          <CrudListPage
            deploymentId={deploymentId}
            table={page.table!}
            tableBizName={page.table_biz_name || page.table!}
            role={role}
            dataMode={dataMode}
            actions={page.actions}
            dataScope={page.data_scope}
            focusPk={focus?.table === page.table ? focus.pk : null}
            focusNonce={focus?.table === page.table ? focus.nonce : 0}
          />
        }
        role={role}
        dataMode={dataMode}
      />
    );
  }

  if (page.kind === "flow" && page.flow_id) {
    return (
      <FlowWizardPage
        deploymentId={deploymentId}
        page={page}
        role={role}
        dataMode={dataMode}
      />
    );
  }

  if (page.kind === "dashboard") {
    return (
      <LayoutGate
        deploymentId={deploymentId}
        pageId={page.id}
        fallback={<DashboardPage deploymentId={deploymentId} page={page} />}
        role={role}
        dataMode={dataMode}
      />
    );
  }

  if (page.kind === "public_form") {
    return <PublicPage deploymentId={deploymentId} pageId={page.id} embedded />;
  }

  const KIND_LABEL: Record<string, string> = {
    public_form: "公开提交页",
    dashboard: "仪表盘",
    flow: "流程页",
  };
  return (
    <Card>
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={
          <div>
            <Text strong>{page.title}</Text>
            <br />
            <Text type="secondary">
              页面「{KIND_LABEL[page.kind] || page.kind}」配置不完整或暂不支持渲染，请到「界面与菜单」检查该页面绑定
            </Text>
          </div>
        }
      />
    </Card>
  );
}

type GateState =
  | { status: "loading" }
  | { status: "ready"; layout: RuntimePageLayout }
  | { status: "failed" };

/** 运行态单次拉取页面布局：有 blocks 用布局渲染；空 blocks / 拉取失败回退默认内容。 */
function LayoutGate({
  deploymentId,
  pageId,
  fallback,
  role,
  dataMode,
}: {
  deploymentId: number;
  pageId: number;
  fallback: React.ReactNode;
  role?: string;
  dataMode?: string;
}) {
  const [state, setState] = useState<GateState>({ status: "loading" });

  useEffect(() => {
    let alive = true;
    setState({ status: "loading" });
    LayoutApi.runtimePageLayout(deploymentId, pageId)
      .then((layout) => alive && setState({ status: "ready", layout }))
      .catch(() => alive && setState({ status: "failed" }));
    return () => {
      alive = false;
    };
  }, [deploymentId, pageId]);

  if (state.status === "loading") {
    return (
      <div style={{ textAlign: "center", padding: 64 }}>
        <Spin tip="加载页面…" />
      </div>
    );
  }
  if (state.status === "failed" || !(state.layout.blocks || []).length) {
    return <>{fallback}</>;
  }
  return (
    <PageLayoutRenderer
      deploymentId={deploymentId}
      pageId={pageId}
      role={role}
      dataMode={dataMode}
      initial={state.layout}
    />
  );
}
