import { useCallback, useEffect, useState } from "react";
import { App, Button, Popconfirm, Select, Space, Statistic, Table, Tag, Typography } from "antd";
import { ClearOutlined } from "@ant-design/icons";
import { AiGenApi, AiGenerationItem, AiGenerationsPage } from "../../api";

const { Text } = Typography;

const KIND_LABEL: Record<string, string> = {
  deploy: "部署提案",
  table: "建表",
  seed: "种子数据",
  crud: "直连补标签",
  qa: "问数据",
};

const KIND_COLOR: Record<string, string> = {
  deploy: "geekblue",
  seed: "green",
  crud: "purple",
  qa: "orange",
};

export function kindLabel(kind: string): string {
  return KIND_LABEL[kind] || kind;
}

export function GenerationTable({
  data,
  loading,
  onRemove,
}: {
  data: AiGenerationsPage | null;
  loading: boolean;
  onRemove?: (item: AiGenerationItem) => void;
}) {
  return (
    <Table<AiGenerationItem>
      size="small"
      rowKey="id"
      loading={loading}
      dataSource={data?.items || []}
      pagination={false}
      columns={[
        { title: "时间", dataIndex: "created_at", width: 160 },
        {
          title: "类型",
          dataIndex: "kind",
          width: 110,
          render: (k: string) => <Tag color={KIND_COLOR[k] || "default"}>{kindLabel(k)}</Tag>,
        },
        {
          title: "状态",
          dataIndex: "status",
          width: 90,
          render: (s: string) => (s === "ok" ? <Tag color="success">成功</Tag> : <Tag color="error">失败</Tag>),
        },
        {
          title: "部署",
          dataIndex: "deployment_name",
          width: 140,
          render: (v: string) => v || "—",
        },
        {
          title: "输入 / 结果",
          key: "summary",
          render: (_: unknown, r) => (
            <div style={{ maxWidth: 420 }}>
              <Text>{r.prompt_summary || "—"}</Text>
              <br />
              <Text type="secondary" style={{ fontSize: 12 }}>
                {r.result_summary || ""}
              </Text>
            </div>
          ),
        },
        {
          title: "用量",
          key: "usage",
          width: 150,
          render: (_: unknown, r) => (
            <Text type="secondary" style={{ fontSize: 12 }}>
              in {r.tokens_in} / out {r.tokens_out}
              <br />
              {r.duration_ms} ms
            </Text>
          ),
        },
        ...(onRemove
          ? [
              {
                title: "操作",
                key: "action",
                width: 80,
                render: (_: unknown, r: AiGenerationItem) => (
                  <Popconfirm title="删除这条记录？" onConfirm={() => onRemove(r)}>
                    <Button type="link" size="small" danger>
                      删除
                    </Button>
                  </Popconfirm>
                ),
              },
            ]
          : []),
      ]}
    />
  );
}

/** M4-4 设置页「AI 生成」Tab（平台管理员）：全量生成历史 + 统计 + 失败/陈旧清理。 */
export default function AiGenerationsTab() {
  const { message } = App.useApp();
  const [data, setData] = useState<AiGenerationsPage | null>(null);
  const [loading, setLoading] = useState(false);
  const [kind, setKind] = useState<string | undefined>();
  const [status, setStatus] = useState<string | undefined>();
  const [page, setPage] = useState(1);

  const load = useCallback(
    (p = page) => {
      setLoading(true);
      AiGenApi.list({ kind, status, page: p, page_size: 20 })
        .then(setData)
        .catch((e) => message.error((e as Error).message))
        .finally(() => setLoading(false));
    },
    [kind, status, page, message],
  );

  useEffect(() => {
    load(1);
    setPage(1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, status]);

  useEffect(() => {
    load(page);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page]);

  const cleanup = async (payload: { status?: string; older_than_days?: number }) => {
    try {
      const r = await AiGenApi.cleanup(payload);
      message.success(`已清理 ${r.deleted} 条`);
      load(page);
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const stats = data?.stats;
  return (
    <div>
      <Space wrap style={{ marginBottom: 12 }}>
        <Select
          allowClear
          placeholder="全部类型"
          style={{ width: 140 }}
          value={kind}
          onChange={setKind}
          options={Object.entries(KIND_LABEL).map(([value, label]) => ({ value, label }))}
        />
        <Select
          allowClear
          placeholder="全部状态"
          style={{ width: 120 }}
          value={status}
          onChange={setStatus}
          options={[
            { value: "ok", label: "成功" },
            { value: "error", label: "失败" },
          ]}
        />
        <Popconfirm title="清理所有失败记录？" onConfirm={() => cleanup({ status: "error" })}>
          <Button icon={<ClearOutlined />}>清理失败记录</Button>
        </Popconfirm>
        <Popconfirm title="清理 30 天前的记录？" onConfirm={() => cleanup({ older_than_days: 30 })}>
          <Button icon={<ClearOutlined />}>清理 30 天前</Button>
        </Popconfirm>
      </Space>

      <Space size="large" style={{ marginBottom: 12 }} wrap>
        <Statistic title="记录数" value={stats?.total ?? 0} />
        <Statistic title="输入 token" value={stats?.tokens_in ?? 0} />
        <Statistic title="输出 token" value={stats?.tokens_out ?? 0} />
        <Statistic title="平均耗时(ms)" value={stats?.avg_duration_ms ?? 0} />
      </Space>

      <GenerationTable
        data={data}
        loading={loading}
        onRemove={async (item) => {
          try {
            await AiGenApi.remove(item.id);
            load(page);
          } catch (e) {
            message.error((e as Error).message);
          }
        }}
      />

      {data && data.total > data.page_size && (
        <Space style={{ marginTop: 12 }}>
          <Button size="small" disabled={page <= 1} onClick={() => setPage(page - 1)}>
            上一页
          </Button>
          <Text type="secondary">
            第 {page} / {Math.ceil(data.total / data.page_size)} 页
          </Text>
          <Button
            size="small"
            disabled={page >= Math.ceil(data.total / data.page_size)}
            onClick={() => setPage(page + 1)}
          >
            下一页
          </Button>
        </Space>
      )}
    </div>
  );
}
