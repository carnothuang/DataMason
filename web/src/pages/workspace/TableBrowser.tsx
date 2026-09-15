import { useEffect, useState } from "react";
import { App, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { DeployApi, RowPage } from "../../api";

const { Text } = Typography;

const HIDDEN_COLS = new Set(["created_by", "updated_by"]);

interface Props {
  deploymentId: number;
  table: string;
  tableBizName: string;
}

/** 工作台内的只读数据浏览（M1-3 能力，M2-4 会整体升级为可操作 CRUD UI）。 */
export default function TableBrowser({ deploymentId, table, tableBizName }: Props) {
  const { message } = App.useApp();
  const [rows, setRows] = useState<RowPage | null>(null);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState<{ field?: string; order?: "asc" | "desc" }>({});

  useEffect(() => {
    setLoading(true);
    DeployApi.rows(deploymentId, table, {
      page,
      page_size: 20,
      sort: sort.field,
      order: sort.order,
    })
      .then(setRows)
      .catch((e) => message.error(e.message))
      .finally(() => setLoading(false));
  }, [deploymentId, table, page, sort.field, sort.order]);

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
    <Table
      rowKey={(r) => String(r.id ?? Math.random())}
      loading={loading}
      dataSource={rows?.data || []}
      columns={columns}
      size="middle"
      title={() => <Text strong>{tableBizName || table}</Text>}
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
  );
}