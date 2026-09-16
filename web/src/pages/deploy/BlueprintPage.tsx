import { useEffect, useState } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Empty,
  List,
  Segmented,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { DeployApi, FieldColumn, TableBlueprint } from "../../api";
import ErGraph from "../../components/ErGraph";
import FieldEditor from "./FieldEditor";

const { Text } = Typography;

interface Props {
  deploymentId: number;
  bizName: string;
  dataMode?: string;
  onBack: () => void;
}

/** M2-8 数据蓝图页：多表导航 + 表字段概览 + ER 关系全域图，
 *  「编辑字段」从原 Drawer 入口升为蓝图「逐张修改」→ diff 提案卡（确认后才 ALTER）。
 *  直连（link）模式外部库结构受保护：只读浏览，不提供编辑。 */
export default function BlueprintPage({ deploymentId, bizName, dataMode, onBack }: Props) {
  const { message } = App.useApp();
  const [tables, setTables] = useState<TableBlueprint[]>([]);
  const [loading, setLoading] = useState(false);
  const [active, setActive] = useState<string | null>(null);
  const [editOpen, setEditOpen] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const linkMode = dataMode === "link";

  useEffect(() => {
    setLoading(true);
    DeployApi.blueprint(deploymentId)
      .then((rows) => {
        setTables(rows);
        if (active === null && rows.length) setActive(rows[0].name);
      })
      .catch((e) => message.error(e.message))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deploymentId, reloadKey]);

  const current = active ? tables.find((t) => t.name === active) : null;

  const fields: ColumnsType<FieldColumn> = [
    { title: "字段", dataIndex: "name", width: 120, render: (v) => <Text code>{v}</Text> },
    {
      title: "业务名",
      dataIndex: "biz_name",
      render: (v) => v || <Text type="secondary">—</Text>,
    },
    {
      title: "类型",
      dataIndex: "type",
      width: 110,
      render: (v, r) => (r.type === "VARCHAR" && r.length ? `${v}(${r.length})` : v),
    },
    {
      title: "可空",
      dataIndex: "nullable",
      width: 60,
      align: "center",
      render: (v) => (v ? "是" : "否"),
    },
    {
      title: "标记",
      dataIndex: "name",
      width: 150,
      render: (_v, r) => {
        const tags: React.ReactNode[] = [];
        if (r.is_pk) tags.push(<Tag color="gold" key="pk">主键</Tag>);
        if (r.is_ownership) tags.push(<Tag key="own">系统</Tag>);
        if (r.is_fk && r.ref_table)
          tags.push(<Tag color="blue" key="fk">外键→{(r.ref_table)}</Tag>);
        return <Space size={4}>{tags}</Space>;
      },
    },
  ];

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <a onClick={onBack}>← 返回部署清单</a>
        <Text strong style={{ fontSize: 16 }}>
          {bizName} · 数据蓝图
        </Text>
        {linkMode ? (
          <Tag color="orange">直连外部库 · 结构受保护（只读）</Tag>
        ) : (
          <Tag color="green">创建模式 · 可编辑</Tag>
        )}
        <Button size="small" onClick={() => setReloadKey((k) => k + 1)}>刷新</Button>
      </Space>

      <Spin spinning={loading}>
        {tables.length === 0 ? (
          <Card>
            <Empty description="该部署还没有可登记的表" />
          </Card>
        ) : (
          <div style={{ display: "flex", gap: 16, alignItems: "flex-start" }}>
            {/* 左：多表导航 */}
            <Card size="small" style={{ width: 230, flexShrink: 0 }}>
              <div style={{ marginBottom: 8 }}>
                <Segmented
                  size="small"
                  block
                  value={active === null ? "graph" : "tables"}
                  onChange={(v) => setActive(v === "graph" ? null : current?.name ?? tables[0].name)}
                  options={[
                    { label: "整体关系图", value: "graph" },
                    { label: "逐张修改", value: "tables" },
                  ]}
                />
              </div>
              {active !== null && (
                <List
                  size="small"
                  style={{ maxHeight: "52vh", overflow: "auto" }}
                  dataSource={tables}
                  renderItem={(t) => (
                    <List.Item
                      style={{
                        padding: "6px 10px",
                        borderRadius: 6,
                        cursor: "pointer",
                        background:
                          t.name === current?.name ? "#fff7e6" : "transparent",
                      }}
                      onClick={() => setActive(t.name)}
                    >
                      <div>
                        <Text strong={t.name === current?.name}>{t.biz_name || t.name}</Text>
                        <br />
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          {t.name} · {t.columns.length} 列
                        </Text>
                      </div>
                    </List.Item>
                  )}
                />
              )}
            </Card>

            {/* 主区 */}
            <div style={{ flex: 1, minWidth: 0 }}>
              {current ? (
                <Card
                  size="small"
                  title={`${current.biz_name || current.name}（${current.name}）`}
                  extra={
                    !linkMode && (
                      <Button type="primary" size="small" onClick={() => setEditOpen(true)}>
                        编辑字段
                      </Button>
                    )
                  }
                >
                  {linkMode && (
                    <Alert
                      type="warning"
                      showIcon
                      style={{ marginBottom: 12 }}
                      message="该部署为直连模式（外部库），结构受保护（DR-05/09）：DataMason 只读浏览、绝不对外部库执行 ALTER。如需改结构请在创建模式另建一套。"
                    />
                  )}
                  <Table<FieldColumn>
                    rowKey="name"
                    size="small"
                    dataSource={current.columns}
                    columns={fields}
                    pagination={false}
                  />
                </Card>
              ) : (
                <Card size="small" title="整体关系图（外键指向）">
                  <ErGraph
                    tables={tables.map((t) => ({
                      name: t.name,
                      biz_name: t.biz_name || t.name,
                      columns: t.columns,
                    }))}
                    width={700}
                    height={420}
                  />
                </Card>
              )}
            </div>
          </div>
        )}
      </Spin>

      {current && !linkMode && (
        <FieldEditor
          deploymentId={deploymentId}
          tableName={current.name}
          bizName={current.biz_name || current.name}
          open={editOpen}
          onClose={() => setEditOpen(false)}
          onChanged={() => {
            setEditOpen(false);
            setReloadKey((k) => k + 1);
          }}
        />
      )}
    </div>
  );
}