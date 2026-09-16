import { useState } from "react";
import { Alert, App, Button, Card, Collapse, Drawer, Empty, Input, Space, Spin, Table, Typography } from "antd";
import { SendOutlined } from "@ant-design/icons";
import { QaAnswer, QaApi } from "../../api";
import ChartCard, { isRenderableChart } from "../../components/ChartCard";

const { Text, Paragraph } = Typography;

interface Props {
  deploymentId: number;
  open: boolean;
  onClose: () => void;
}

/** M4-2 问数据（形态 C）：工作台右上角入口 → 抽屉里「输入 → 视图卡」。
 *  视图卡 = 一句话结论 + 图表/数据表 + 可展开的「它怎么查的」SQL（默认收起，不吓人）。
 *  图表规格非法/数据不可数值化 → 退化为表格（不出错）；操作员不开放（由 Workspace 控制入口）。 */
export default function AskData({ deploymentId, open, onClose }: Props) {
  const { message } = App.useApp();
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [items, setItems] = useState<QaAnswer[]>([]);
  const [error, setError] = useState("");

  const ask = async () => {
    const q = question.trim();
    if (!q) {
      message.warning("请输入一个问题");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const answer = await QaApi.ask(deploymentId, q);
      setItems((prev) => [answer, ...prev]);
      setQuestion("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <Drawer
      title="问数据"
      width={620}
      open={open}
      onClose={onClose}
      styles={{ body: { background: "#fafafa" } }}
    >
      <Space.Compact style={{ width: "100%", marginBottom: 12 }}>
        <Input
          placeholder="用一句话问你的数据，例如：按分类统计商品数量"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onPressEnter={ask}
          disabled={loading}
          maxLength={500}
        />
        <Button type="primary" icon={<SendOutlined />} loading={loading} onClick={ask}>
          问一句
        </Button>
      </Space.Compact>

      {error && (
        <Alert type="error" showIcon style={{ marginBottom: 12 }} message={error} />
      )}

      {loading && (
        <div style={{ textAlign: "center", padding: "16px 0" }}>
          <Spin tip="正在理解并查询…" />
        </div>
      )}

      {!loading && items.length === 0 && (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="还没有问答记录，输入一个问题试试（只读查询，不会改动数据）"
        />
      )}

      {items.map((answer, idx) => (
        <ViewCard key={idx} answer={answer} />
      ))}
    </Drawer>
  );
}

function ViewCard({ answer }: { answer: QaAnswer }) {
  const renderable = isRenderableChart(answer.chart, answer.columns, answer.rows);
  const dataColumns = answer.columns.map((col) => ({
    title: col,
    dataIndex: col,
    key: col,
    ellipsis: true,
    render: (value: unknown) => (value == null ? "" : String(value)),
  }));

  const rowsTable = (
    <Table
      size="small"
      rowKey={(_, i) => String(i)}
      columns={dataColumns}
      dataSource={answer.rows}
      pagination={answer.rows.length > 8 ? { pageSize: 8, size: "small" } : false}
      scroll={{ x: "max-content" }}
    />
  );

  return (
    <Card
      size="small"
      title={answer.title}
      extra={<Text type="secondary">{answer.row_count} 行</Text>}
      style={{ marginBottom: 12 }}
    >
      <Paragraph style={{ marginBottom: 12 }}>{answer.conclusion || "（无结论）"}</Paragraph>

      {renderable && answer.chart ? (
        <>
          <ChartCard chart={answer.chart} rows={answer.rows} />
          <Collapse
            ghost
            size="small"
            items={[
              { key: "rows", label: `数据（${answer.row_count} 行）`, children: rowsTable },
            ]}
          />
        </>
      ) : (
        rowsTable
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
                  background: "#f6f6f6",
                  borderRadius: 6,
                  padding: 10,
                  fontSize: 12,
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-all",
                }}
              >
                {answer.sql}
              </pre>
            ),
          },
        ]}
      />
    </Card>
  );
}
