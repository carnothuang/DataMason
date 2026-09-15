import { useEffect, useState } from "react";
import { App, Button, Empty, Segmented, Space, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { DeployApi, RowPage, TableInfo } from "../../api";
import FieldEditor from "./FieldEditor";

const { Text } = Typography;

interface Props {
  deploymentId: number;
  bizName: string;
  onBack: () => void;
}

const HIDDEN_COLS = new Set(["created_by", "updated_by"]);

export default function DataViewer({ deploymentId, bizName, onBack }: Props) {
  const { message } = App.useApp();
  const [tables, setTables] = useState<TableInfo[]>([]);
  const [active, setActive] = useState<string>("");
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState<{ field?: string; order?: "asc" | "desc" }>({});
  const [rows, setRows] = useState<RowPage | null>(null);
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    DeployApi.tables(deploymentId)
      .then((list) => {
        setTables(list);
        if (list.length) setActive(list[0].name);
      })
      .catch((e) => message.error(e.message));
  }, [deploymentId, reloadKey]);

  useEffect(() => {
    if (!active) return;
    setLoading(true);
    DeployApi.rows(deploymentId, active, {
      page,
      page_size: 20,
      sort: sort.field,
      order: sort.order,
    })
      .then(setRows)
      .catch((e) => message.error(e.message))
      .finally(() => setLoading(false));
  }, [deploymentId, active, page, sort.field, sort.order, reloadKey]);

  const columns: ColumnsType<Record<string, unknown>> = rows
    ? rows.columns
        .filter((c) => !HIDDEN_COLS.has(c))
        .map((c) => ({
          title: c,
          dataIndex: c,
          key: c,
          ellipsis: true,
          render: (v: unknown) =>
            v === null || v === undefined ? <Text type="secondary">—</Text> : String(v),
          sorter: true,
          sortOrder: sort.field === c ? (sort.order === "desc" ? "descend" : "ascend") : null,
        }))
    : [];

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <a onClick={onBack}>← 返回部署清单</a>
        <Text strong style={{ fontSize: 16 }}>
          {bizName}
        </Text>
        <Tag color="green">运行中</Tag>
      </Space>

      {tables.length === 0 ? (
        <Empty description="该部署还没有表" />
      ) : (
        <>
          <Space style={{ marginBottom: 12 }} wrap>
            <Text type="secondary">数据表：</Text>
            <Segmented
              value={active}
              onChange={(v) => {
                setActive(String(v));
                setPage(1);
                setSort({});
              }}
              options={tables.map((t) => ({
                label: t.biz_name || t.name,
                value: t.name,
              }))}
            />
            <Button
              size="small"
              onClick={() => setEditing(true)}
              disabled={!active}
              style={{ marginLeft: 8 }}
            >
              编辑字段
            </Button>
          </Space>
          <Table
            rowKey={(r) => JSON.stringify(r.id ?? Math.random())}
            loading={loading}
            dataSource={rows?.data || []}
            columns={columns}
            size="middle"
            scroll={{ x: "max-content" }}
            pagination={{
              current: page,
              pageSize: 20,
              total: rows?.total || 0,
              showTotal: (t) => `共 ${t} 条`,
              onChange: (p) => setPage(p),
            }}
            onChange={(_p, _f, sorter) => {
              const s = Array.isArray(sorter) ? sorter[0] : sorter;
              if (s && s.field && s.order) {
                setSort({ field: String(s.field), order: s.order === "descend" ? "desc" : "asc" });
              } else {
                setSort({});
              }
            }}
          />
          {active && (
            <FieldEditor
              deploymentId={deploymentId}
              tableName={active}
              bizName={tables.find((t) => t.name === active)?.biz_name || active}
              open={editing}
              onClose={() => setEditing(false)}
              onChanged={() => {
                setSort({});
                setPage(1);
                setReloadKey((k) => k + 1);
              }}
            />
          )}
        </>
      )}
    </div>
  );
}
