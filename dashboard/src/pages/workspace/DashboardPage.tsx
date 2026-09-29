import { useCallback, useEffect, useState } from "react";
import { Button, Card, Collapse, Empty, Space, Spin, Table, Typography } from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import { DashboardApi, DashboardRun, WorkspacePage } from "../../api";
import ChartCard, { isRenderableChart } from "../../components/ChartCard";
import { maskText, useDemo } from "./demo";

const { Text } = Typography;

interface Props {
  deploymentId: number;
  page: WorkspacePage;
}

/** M4-3 钉住报表（dashboard 页）运行态：进入即重跑存储的只读 SQL（数据为最新），
 *  图表规格可渲染则出图，否则退化为表格；「它怎么查的」按需披露 SQL。
 *  M3-3 演示模式：脱敏开关对报表数据同样生效（只改展示层）。 */
export default function DashboardPage({ deploymentId, page }: Props) {
  const { masked } = useDemo();
  const [data, setData] = useState<DashboardRun | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    setLoading(true);
    setError("");
    DashboardApi.run(deploymentId, page.id)
      .then(setData)
      .catch((e) => setError((e as Error).message))
      .finally(() => setLoading(false));
  }, [deploymentId, page.id]);

  useEffect(() => {
    load();
  }, [load]);

  const renderable = data ? isRenderableChart(data.chart, data.columns, data.rows) : false;
  const columns = (data?.columns || []).map((col) => ({
    title: col,
    dataIndex: col,
    key: col,
    ellipsis: true,
    render: (value: unknown) =>
      value == null ? "" : String(masked ? maskText(value) : value),
  }));

  return (
    <Card
      title={page.title || data?.title || "报表"}
      extra={
        <Space>
          {data && <Text type="secondary">{data.row_count} 行</Text>}
          <Button size="small" icon={<ReloadOutlined />} loading={loading} onClick={load}>
            刷新
          </Button>
        </Space>
      }
    >
      {loading && !data ? (
        <div style={{ textAlign: "center", padding: "32px 0" }}>
          <Spin tip="正在运行报表…" />
        </div>
      ) : error ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={error} />
      ) : data ? (
        <>
          {renderable && data.chart ? (
            <>
              <ChartCard chart={data.chart} rows={data.rows} />
              <Collapse
                ghost
                size="small"
                items={[
                  {
                    key: "rows",
                    label: `数据（${data.row_count} 行）`,
                    children: (
                      <Table
                        size="small"
                        rowKey={(_, i) => String(i)}
                        columns={columns}
                        dataSource={data.rows}
                        pagination={data.rows.length > 8 ? { pageSize: 8, size: "small" } : false}
                        scroll={{ x: "max-content" }}
                      />
                    ),
                  },
                ]}
              />
            </>
          ) : (
            <Table
              size="small"
              rowKey={(_, i) => String(i)}
              columns={columns}
              dataSource={data.rows}
              pagination={data.rows.length > 10 ? { pageSize: 10, size: "small" } : false}
              scroll={{ x: "max-content" }}
            />
          )}
          <Collapse
            ghost
            size="small"
            items={[
              {
                key: "sql",
                label: "它怎么查的",
                children: (
                  <pre
                    style={{
                      margin: 0,
                      background: "var(--fn-bg-code)",
                      borderRadius: 6,
                      padding: 10,
                      fontSize: 12,
                      whiteSpace: "pre-wrap",
                      wordBreak: "break-all",
                    }}
                  >
                    {data.sql}
                  </pre>
                ),
              },
            ]}
          />
        </>
      ) : null}
    </Card>
  );
}
