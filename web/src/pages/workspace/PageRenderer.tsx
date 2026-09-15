import { Card, Empty, Typography } from "antd";
import { WorkspacePage } from "../../api";
import CrudListPage from "./CrudListPage";

const { Text } = Typography;

interface Props {
  deploymentId: number;
  page: WorkspacePage | null;
}

/** 按 page_spec 渲染内容区：crud_list → 元数据驱动 CRUD 列表（M2-4）；其余类型 v0 占位（M3 起补全）。 */
export default function PageRenderer({ deploymentId, page }: Props) {
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
      />
    );
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