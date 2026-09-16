import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Empty,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
} from "antd";
import { CopyOutlined, KeyOutlined, PlayCircleOutlined, PlusOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { ApiKeyApi, ApiKeyItem, WorkspaceApi, WorkspacePage } from "../../api";

const { Text, Paragraph } = Typography;

interface Props {
  deploymentId: number;
  canManageKeys: boolean;
}

const ENDPOINTS = [
  { method: "GET", path: "/api/v1/app/{id}/api/meta", desc: "表和列的元数据（字段名/类型/外键）" },
  { method: "GET", path: "/api/v1/app/{id}/api/tables/{table}/rows", desc: "分页查询（page/page_size/sort/order/search/按列过滤）" },
  { method: "GET", path: "/api/v1/app/{id}/api/tables/{table}/rows/{pk}", desc: "按主键读取单行" },
  { method: "POST", path: "/api/v1/app/{id}/api/tables/{table}/rows", desc: "新增一行（写路径，进操作日志）" },
  { method: "PUT", path: "/api/v1/app/{id}/api/tables/{table}/rows/{pk}", desc: "更新一行（写路径，进操作日志）" },
  { method: "DELETE", path: "/api/v1/app/{id}/api/tables/{table}/rows/{pk}", desc: "删除一行（写路径，进操作日志）" },
  { method: "GET", path: "/api/v1/app/{id}/api/tables/{table}/refs/{fk_col}", desc: "外键下拉数据" },
];

/** M3-8 工作台「API 文档」（DR-11）：把部署的 CRUD REST 开放给外部系统。
 *  key 管理创建人独有；文档含端点清单/示例 + 可试调用（返回真实数据）。 */
export default function ApiDocsPage({ deploymentId, canManageKeys }: Props) {
  const { message } = App.useApp();
  const [pages, setPages] = useState<WorkspacePage[]>([]);
  const [keys, setKeys] = useState<ApiKeyItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [createOpen, setCreateOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);
  const [freshKey, setFreshKey] = useState<string>("");

  const [table, setTable] = useState<string | undefined>(undefined);
  const [useKey, setUseKey] = useState(false);
  const [keyInput, setKeyInput] = useState("");
  const [trying, setTrying] = useState(false);
  const [result, setResult] = useState("");

  const origin = typeof window !== "undefined" ? window.location.origin : "";

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const info = await WorkspaceApi.info(deploymentId).catch(() => null);
      if (info) setPages(info.pages);
      if (canManageKeys) {
        setKeys(await ApiKeyApi.list(deploymentId));
      }
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [deploymentId, canManageKeys, message]);

  useEffect(() => {
    void load();
  }, [load]);

  const tables = useMemo(
    () => pages.filter((p) => p.kind === "crud_list" && p.table).map((p) => p.table as string),
    [pages],
  );

  const exampleTable = table || tables[0] || "your_table";
  const curl = `curl -H "X-API-Key: dm_你的密钥" \\
  "${origin}/api/v1/app/${deploymentId}/api/tables/${exampleTable}/rows?page_size=3"`;

  const createKey = async () => {
    const name = newName.trim();
    if (!name) {
      message.error("请填写密钥名称（用途）");
      return;
    }
    setCreating(true);
    try {
      const created = await ApiKeyApi.create(deploymentId, name);
      setFreshKey(created.key);
      setNewName("");
      setKeyInput(created.key);
      setCreateOpen(false);
      message.success("已发放密钥（明文仅显示这一次，请妥善保存）");
      await load();
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setCreating(false);
    }
  };

  const revoke = async (id: number) => {
    try {
      await ApiKeyApi.revoke(deploymentId, id);
      message.success("已吊销，密钥立即失效");
      await load();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const tryCall = async () => {
    if (!table) {
      message.error("请先选择一张表");
      return;
    }
    setTrying(true);
    setResult("");
    try {
      const headers: Record<string, string> = {};
      if (useKey && keyInput.trim()) headers["X-API-Key"] = keyInput.trim();
      const r = await fetch(
        `/api/v1/app/${deploymentId}/api/tables/${encodeURIComponent(table)}/rows?page_size=3`,
        { headers },
      );
      const body = await r.json().catch(() => null);
      setResult(`HTTP ${r.status}\n\n${JSON.stringify(body, null, 2)}`);
    } catch (e) {
      setResult(`请求失败：${(e as Error).message}`);
    } finally {
      setTrying(false);
    }
  };

  const endpointCols: ColumnsType<(typeof ENDPOINTS)[number]> = [
    {
      title: "方法",
      dataIndex: "method",
      width: 90,
      render: (m: string) => (
        <Tag color={m === "GET" ? "blue" : m === "POST" ? "green" : m === "PUT" ? "orange" : "red"}>
          {m}
        </Tag>
      ),
    },
    {
      title: "路径",
      dataIndex: "path",
      render: (p: string) => <Text code>{p.replace("{id}", String(deploymentId))}</Text>,
    },
    { title: "说明", dataIndex: "desc" },
  ];

  const keyCols: ColumnsType<ApiKeyItem> = [
    { title: "名称", dataIndex: "name", width: 160 },
    {
      title: "密钥",
      dataIndex: "masked",
      width: 180,
      render: (m: string, r) => (
        <Space>
          <Text code>{m}</Text>
          {r.status === "revoked" ? <Tag>已吊销</Tag> : <Tag color="green">有效</Tag>}
        </Space>
      ),
    },
    { title: "创建人", dataIndex: "created_by_display", width: 120 },
    { title: "创建时间", dataIndex: "created_at", width: 170, render: (v: string) => <Text type="secondary">{v}</Text> },
    {
      title: "最近使用",
      dataIndex: "last_used_at",
      width: 170,
      render: (v: string) => (v ? <Text type="secondary">{v}</Text> : <Text type="secondary">—</Text>),
    },
    {
      title: "操作",
      key: "_ops",
      width: 100,
      render: (_: unknown, r) =>
        r.status === "active" ? (
          <Popconfirm title="吊销该密钥？" description="吊销后立即失效。" onConfirm={() => revoke(r.id)}>
            <Button type="link" size="small" danger>
              吊销
            </Button>
          </Popconfirm>
        ) : null,
    },
  ];

  if (loading) {
    return (
      <div style={{ display: "flex", justifyContent: "center", padding: 48 }}>
        <Spin tip="加载 API 文档…" />
      </div>
    );
  }

  return (
    <Space direction="vertical" style={{ width: "100%" }} size={16}>
      <Alert
        type="info"
        showIcon
        message="把系统的数据接口开放给外部程序"
        description={
          <span>
            使用部署级 API 密钥调用 REST 接口，读写会自动进操作日志。
            请求头带 <Text code>X-API-Key: dm_…</Text>（或 <Text code>Authorization: Bearer dm_…</Text>）。
          </span>
        }
      />

      <Card size="small" title="接入信息">
        <Space direction="vertical" style={{ width: "100%" }} size={8}>
          <div>
            <Text strong>Base URL：</Text>
            <Text code copyable>
              {origin}/api/v1/app/{deploymentId}/api
            </Text>
          </div>
          <div>
            <Text strong>认证头：</Text>
            <Text code>X-API-Key: dm_你的密钥</Text>
          </div>
        </Space>
      </Card>

      <Card size="small" title="端点清单">
        <Table
          rowKey={(r) => r.method + r.path}
          size="small"
          pagination={false}
          columns={endpointCols}
          dataSource={ENDPOINTS}
        />
      </Card>

      <Card size="small" title="调用示例">
        <pre
          style={{
            background: "#fafafa",
            border: "1px solid #f0f0f0",
            borderRadius: 6,
            padding: 12,
            margin: 0,
            overflowX: "auto",
          }}
        >
          {curl}
        </pre>
      </Card>

      {canManageKeys && (
        <Card
          size="small"
          title={
            <Space>
              <KeyOutlined />
              API 密钥（创建人管理）
            </Space>
          }
          extra={
            <Button type="primary" size="small" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
              发放密钥
            </Button>
          }
        >
          {freshKey && (
            <Alert
              type="success"
              showIcon
              style={{ marginBottom: 12 }}
              message="密钥已生成（明文仅显示这一次）"
              description={
                <Space direction="vertical" style={{ width: "100%" }}>
                  <Text code copyable={{ text: freshKey, icon: <CopyOutlined /> }}>
                    {freshKey}
                  </Text>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    请立即复制保存；关闭后无法再次查看（列表仅显示掩码）。
                  </Text>
                </Space>
              }
            />
          )}
          {keys.length ? (
            <Table rowKey={(r) => r.id} size="small" pagination={false} columns={keyCols} dataSource={keys} />
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有密钥，点右上角「发放密钥」" />
          )}
        </Card>
      )}

      <Card size="small" title="试调用（返回真实数据）">
        <Space direction="vertical" style={{ width: "100%" }} size={10}>
          <Space wrap>
            <Text>表</Text>
            <Select
              style={{ width: 220 }}
              placeholder="选择数据表"
              value={table}
              options={tables.map((t) => ({ value: t, label: t }))}
              onChange={setTable}
            />
            <Select
              style={{ width: 150 }}
              value={useKey ? "key" : "session"}
              onChange={(v) => setUseKey(v === "key")}
              options={[
                { value: "session", label: "用当前登录态" },
                { value: "key", label: "用 API 密钥" },
              ]}
            />
            {useKey && (
              <Input
                style={{ width: 320 }}
                placeholder="粘贴 dm_ 密钥"
                value={keyInput}
                onChange={(e) => setKeyInput(e.target.value)}
              />
            )}
            <Button type="primary" icon={<PlayCircleOutlined />} loading={trying} onClick={tryCall}>
              试调用
            </Button>
          </Space>
          <Text type="secondary" style={{ fontSize: 12 }}>
            调用 <Text code>GET .../tables/{"{table}"}/rows?page_size=3</Text>
            ；「用 API 密钥」时以 X-API-Key 头认证（演示 key 通道）。
          </Text>
          {result && (
            <pre
              style={{
                background: "#1e1e1e",
                color: "#d4d4d4",
                borderRadius: 6,
                padding: 12,
                margin: 0,
                maxHeight: 320,
                overflow: "auto",
              }}
            >
              {result}
            </pre>
          )}
        </Space>
      </Card>

      <Modal
        title="发放 API 密钥"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={createKey}
        confirmLoading={creating}
        okText="生成"
        cancelText="取消"
        destroyOnClose
      >
        <Paragraph type="secondary" style={{ fontSize: 12 }}>
          给密钥起个用途名（如「订单同步」），便于在操作日志里识别来源。
        </Paragraph>
        <Input
          placeholder="密钥名称"
          value={newName}
          maxLength={64}
          onChange={(e) => setNewName(e.target.value)}
          onPressEnter={createKey}
        />
      </Modal>
    </Space>
  );
}