import { Card, Empty, Typography } from "antd";
import { WorkspacePage } from "../../api";
import CrudListPage from "./CrudListPage";
import FlowWizardPage from "./FlowWizardPage";
import DashboardPage from "./DashboardPage";
import PublicPage from "../PublicPage";
import type { RecordFocus } from "./Workspace";

const { Text } = Typography;

interface Props {
  deploymentId: number;
  page: WorkspacePage | null;
  role?: string;
  dataMode?: string;
  focus?: RecordFocus | null;
}

/** 按 page_spec 渲染内容区：crud_list → 元数据驱动 CRUD 列表（M2-4）；其余类型 v0 占位（M3 起补全）。
 *  M2-5：role=operator 时列表受「我的记录」过滤（服务端强制 created_by=自己，前端仅提示）。
 *  M2-6：日志→记录「查看这条记录」聚焦跳转（focus 命中本页表时传给 CRUD 列表定位行）。
 *  M2-7：直连模式缺归属列表在 CRUD 列表内降级明示。 */
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
      <CrudListPage
        deploymentId={deploymentId}
        table={page.table}
        tableBizName={page.table_biz_name || page.table}
        role={role}
        dataMode={dataMode}
        focusPk={focus?.table === page.table ? focus.pk : null}
        focusNonce={focus?.table === page.table ? focus.nonce : 0}
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
    return <DashboardPage deploymentId={deploymentId} page={page} />;
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
              页面类型「{KIND_LABEL[page.kind] || page.kind}」将在后续版本提供
            </Text>
          </div>
        }
      />
    </Card>
  );
}