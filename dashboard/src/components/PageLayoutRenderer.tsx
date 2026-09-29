import { useEffect, useState } from "react";
import GridLayout from "react-grid-layout";
import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";
import { Alert, Card, Empty, Spin, Tag, Typography } from "antd";
import type {
  PageLayoutBlock,
  RuntimePageLayout,
  StatResult,
  TableUiConfigPayload,
} from "../api";
import { LayoutApi } from "../api";
import CrudListPage from "../pages/workspace/CrudListPage";

const { Text, Paragraph } = Typography;

interface Props {
  deploymentId: number;
  pageId: number;
  role?: string;
  dataMode?: string;
  initial?: RuntimePageLayout;
}

interface Resolved {
  layout: RuntimePageLayout | null;
  failed: boolean;
}

/** 0.15.0 M9-8：运行态按布局 schema 渲染自定义页面 / 仪表盘。
 *  读运行态只读布局 → RGL 静态栅格定位区块 → table/stat/html 渲染。
 *  拉取或解析异常 → null（调用方据此回退默认渲染）。
 *  initial 为调用方已预取的布局，传入时不再重复请求。 */
export default function PageLayoutRenderer({ deploymentId, pageId, role, dataMode, initial }: Props) {
  const [res, setRes] = useState<Resolved>({
    layout: initial || null,
    failed: false,
  });

  useEffect(() => {
    if (initial) return;
    let alive = true;
    setRes({ layout: null, failed: false });
    LayoutApi.runtimePageLayout(deploymentId, pageId)
      .then((layout) => alive && setRes({ layout, failed: false }))
      .catch(() => alive && setRes({ layout: null, failed: true }));
    return () => {
      alive = false;
    };
  }, [deploymentId, pageId, initial]);

  if (res.failed) return null;
  if (!res.layout) {
    return (
      <div style={{ textAlign: "center", padding: 64 }}>
        <Spin tip="加载页面布局…" />
      </div>
    );
  }

  const blocks = res.layout.blocks || [];
  if (!blocks.length) return null;

  const items = blocks.map((b, i) => ({
    i: b.key,
    x: b.layout?.x ?? 0,
    y: b.layout?.y ?? i * 4,
    w: b.layout?.w ?? 12,
    h: b.layout?.h ?? 4,
    static: true,
  }));

  return (
    <div style={{ height: "100%", overflow: "auto" }}>
      {res.layout.degraded && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message="该页面布局部分失效，已按当前配置尽量展示，请联系创建人重新保存布局"
        />
      )}
      <GridLayout
        className="dm-layout-runtime"
        layout={items}
        cols={12}
        rowHeight={40}
        width={1100}
        margin={[12, 12]}
        isDraggable={false}
        isResizable={false}
      >
        {blocks.map((b) => (
          <div key={b.key} style={{ height: "100%" }}>
            <BlockCard
              block={b}
              deploymentId={deploymentId}
              role={role}
              dataMode={dataMode}
              uiConfig={res.layout!.ui_configs?.[b.props.table || ""]}
            />
          </div>
        ))}
      </GridLayout>
    </div>
  );
}

function BlockCard({
  block,
  deploymentId,
  role,
  dataMode,
  uiConfig,
}: {
  block: PageLayoutBlock;
  deploymentId: number;
  role?: string;
  dataMode?: string;
  uiConfig?: TableUiConfigPayload;
}) {
  if (block.x_component === "table" && block.props.table) {
    return (
      <CrudListPage
        deploymentId={deploymentId}
        table={block.props.table}
        tableBizName={block.props.table}
        role={role}
        dataMode={dataMode}
        blockProps={block.props}
        uiConfig={uiConfig}
      />
    );
  }
  if (block.x_component === "stat") {
    return <StatCard block={block} deploymentId={deploymentId} />;
  }
  if (block.x_component === "html") {
    return (
      <Card size="small" style={{ height: "100%", overflow: "auto" }}>
        <Paragraph style={{ marginBottom: 0, whiteSpace: "pre-wrap" }}>
          {block.props.content || ""}
        </Paragraph>
      </Card>
    );
  }
  return (
    <Card size="small" style={{ height: "100%", overflow: "auto" }}>
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={
          <span>
            <Text strong>详情区块</Text>
            <br />
            <Text type="secondary">请先在列表中选择一条记录查看详情</Text>
          </span>
        }
      />
      <Tag>{block.props.table || "未绑定表"}</Tag>
    </Card>
  );
}

function StatCard({
  block,
  deploymentId,
}: {
  block: PageLayoutBlock;
  deploymentId: number;
}) {
  const [stat, setStat] = useState<StatResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const p = block.props;

  useEffect(() => {
    if (!p.table) {
      setError("未选择数据表");
      setLoading(false);
      return;
    }
    let alive = true;
    setLoading(true);
    setError("");
    LayoutApi.stat(deploymentId, p.table, {
      aggregation: p.aggregation || "count",
      column: p.column || undefined,
    })
      .then((r) => alive && setStat(r))
      .catch((e) => alive && setError((e as Error).message))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [deploymentId, p.table, p.aggregation, p.column]);

  const shown = stat?.value;
  return (
    <Card size="small" style={{ height: "100%" }} styles={{ body: { height: "100%" } }}>
      <div
        style={{
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
        }}
      >
        <Text type="secondary" style={{ fontSize: 13 }}>
          {p.title || "统计"}
        </Text>
        {loading ? (
          <Spin size="small" style={{ marginTop: 8 }} />
        ) : error ? (
          <Text type="danger" style={{ fontSize: 12 }}>
            {error}
          </Text>
        ) : (
          <Text style={{ fontSize: 30, fontWeight: 700, lineHeight: 1.2 }}>
            {shown == null ? "—" : Number.isInteger(shown) ? shown : shown.toFixed(2)}
          </Text>
        )}
      </div>
    </Card>
  );
}
