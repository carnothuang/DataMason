import { useCallback, useEffect, useState } from "react";
import { Alert, App, Button, Card, Collapse, Drawer, Empty, Input, List, Popconfirm, Space, Spin, Table, Tag, Typography } from "antd";
import { PushpinOutlined, SendOutlined } from "@ant-design/icons";
import { DashboardRun, QaAnswer, QaApi, QaPin } from "../../api";
import ChartCard, { isRenderableChart } from "../../components/ChartCard";
import { maskText, useDemo } from "./demo";

const { Text, Paragraph } = Typography;

interface Props {
  deploymentId: number;
  open: boolean;
  onClose: () => void;
  canPin: boolean;
  onPinned: () => void;
}

/** M4-2/M4-3 问数据（形态 C）：工作台右上角入口 → 抽屉里「输入 → 视图卡」。
 *  视图卡 = 一句话结论 + 图表/数据表 + 可展开的「它怎么查的」SQL（默认收起，不吓人）。
 *  图表规格非法/数据不可数值化 → 退化为表格（不出错）；操作员不开放（由 Workspace 控制入口）。
 *  M4-3：创建人/管理员可把视图卡「钉住」为常驻报表（dashboard 页），并可在此取消钉住。 */
export default function AskData({ deploymentId, open, onClose, canPin, onPinned }: Props) {
  const { message } = App.useApp();
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [items, setItems] = useState<QaAnswer[]>([]);
  const [error, setError] = useState("");
  const [pins, setPins] = useState<QaPin[]>([]);

  const loadPins = useCallback(() => {
    if (!canPin) return;
    QaApi.pins(deploymentId).then(setPins).catch(() => setPins([]));
  }, [deploymentId, canPin]);

  useEffect(() => {
    if (open) loadPins();
  }, [open, loadPins]);

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

  const pin = async (answer: QaAnswer) => {
    try {
      const result = await QaApi.pin(deploymentId, {
        title: answer.title,
        sql: answer.sql,
        chart: answer.chart,
        question: answer.question,
      });
      message.success(result.duplicate ? "该报表已钉住" : "已钉到工作台");
      loadPins();
      onPinned();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const unpin = async (p: QaPin) => {
    try {
      await QaApi.unpin(deploymentId, p.page_id);
      message.success("已取消钉住");
      loadPins();
      onPinned();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const pinnedSqls = new Set(pins.map((p) => p.sql));

  return (
    <Drawer
      title="问数据"
      width={620}
      open={open}
      onClose={onClose}
      destroyOnClose
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

      {error && <Alert type="error" showIcon style={{ marginBottom: 12 }} message={error} />}

      {canPin && pins.length > 0 && (
        <Card size="small" title={`已钉住的报表（${pins.length}）`} style={{ marginBottom: 12 }}>
          <List
            size="small"
            dataSource={pins}
            renderItem={(p) => (
              <List.Item
                actions={[
                  <Popconfirm key="unpin" title="取消钉住？" onConfirm={() => unpin(p)}>
                    <Button type="link" size="small" danger>
                      取消钉住
                    </Button>
                  </Popconfirm>,
                ]}
              >
                <Space>
                  <PushpinOutlined style={{ color: "#C15F3C" }} />
                  <Text>{p.title}</Text>
                </Space>
              </List.Item>
            )}
          />
        </Card>
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
        <ViewCard
          key={idx}
          answer={answer}
          canPin={canPin && !pinnedSqls.has(answer.sql)}
          pinned={canPin && pinnedSqls.has(answer.sql)}
          onPin={() => pin(answer)}
        />
      ))}
    </Drawer>
  );
}

function RowsTable({ answer }: { answer: QaAnswer | DashboardRun }) {
  const { masked } = useDemo();
  const columns = answer.columns.map((col) => ({
    title: col,
    dataIndex: col,
    key: col,
    ellipsis: true,
    render: (value: unknown) =>
      value == null ? "" : String(masked ? maskText(value) : value),
  }));
  return (
    <Table
      size="small"
      rowKey={(_, i) => String(i)}
      columns={columns}
      dataSource={answer.rows}
      pagination={answer.rows.length > 8 ? { pageSize: 8, size: "small" } : false}
      scroll={{ x: "max-content" }}
    />
  );
}

function SqlBlock({ sql }: { sql: string }) {
  return (
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
      {sql}
    </pre>
  );
}

function ViewCard({
  answer,
  canPin,
  pinned,
  onPin,
}: {
  answer: QaAnswer;
  canPin: boolean;
  pinned: boolean;
  onPin: () => void;
}) {
  const renderable = isRenderableChart(answer.chart, answer.columns, answer.rows);
  const extra = (
    <Space>
      <Text type="secondary">{answer.row_count} 行</Text>
      {pinned ? (
        <Tag icon={<PushpinOutlined />} color="orange">
          已钉住
        </Tag>
      ) : canPin ? (
        <Button size="small" icon={<PushpinOutlined />} onClick={onPin}>
          钉住
        </Button>
      ) : null}
    </Space>
  );

  return (
    <Card size="small" title={answer.title} extra={extra} style={{ marginBottom: 12 }}>
      <Paragraph style={{ marginBottom: 12 }}>{answer.conclusion || "（无结论）"}</Paragraph>

      {renderable && answer.chart ? (
        <>
          <ChartCard chart={answer.chart} rows={answer.rows} />
          <Collapse
            ghost
            size="small"
            items={[{ key: "rows", label: `数据（${answer.row_count} 行）`, children: <RowsTable answer={answer} /> }]}
          />
        </>
      ) : (
        <RowsTable answer={answer} />
      )}

      <Collapse
        ghost
        size="small"
        items={[{ key: "sql", label: "它怎么查的", children: <SqlBlock sql={answer.sql} /> }]}
      />
    </Card>
  );
}
