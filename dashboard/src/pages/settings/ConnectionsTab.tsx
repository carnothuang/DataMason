import { useCallback, useEffect, useMemo, useState } from "react";
import {
  App,
  Button,
  Drawer,
  Empty,
  Form,
  Input,
  InputNumber,
  Popconfirm,
  Radio,
  Segmented,
  Spin,
} from "antd";
import {
  ApiOutlined,
  AppstoreOutlined,
  CloudServerOutlined,
  DatabaseOutlined,
  DeleteOutlined,
  GlobalOutlined,
  PlusOutlined,
  ReloadOutlined,
  SearchOutlined,
  UnorderedListOutlined,
} from "@ant-design/icons";
import { Connection, ConnectionsApi } from "../../api";
import { ResizableTable } from "../../components/ResizableTable";
import { useTranslation } from "react-i18next";
import "./shared.css";

const VIEW_KEY = "datamason:connections:view";
const TABLE_WIDTH_KEY = "datamason:connections:table-widths";

type ViewMode = "card" | "table";
type Dialect = "sqlite" | "mysql" | "postgresql";

interface FormValues {
  name: string;
  dialect: Dialect;
  db_path: string;
  host: string;
  port: number;
  user: string;
  password: string;
  database: string;
  create: boolean;
}

const DIALECT_ICON: Record<Dialect, JSX.Element> = {
  sqlite: <DatabaseOutlined />,
  mysql: <CloudServerOutlined />,
  postgresql: <GlobalOutlined />,
};

function statusMeta(status: string) {
  if (status === "ok") {
    return { tone: "#52c41a", key: "connections.statusOk" };
  }
  if (status === "error") {
    return { tone: "#ff4d4f", key: "connections.statusError" };
  }
  return { tone: "#bfbfbf", key: "connections.statusUnknown" };
}

export default function ConnectionsTab() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [rows, setRows] = useState<Connection[]>([]);
  const [loading, setLoading] = useState(false);
  const [keyword, setKeyword] = useState("");
  const [view, setView] = useState<ViewMode>(() => {
    const v = localStorage.getItem(VIEW_KEY);
    return v === "card" || v === "table" ? v : "table";
  });
  const [open, setOpen] = useState(false);
  const [testing, setTesting] = useState<number | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [isMobile, setIsMobile] = useState(
    typeof window !== "undefined" ? window.innerWidth < 768 : false
  );
  const [form] = Form.useForm<FormValues>();
  const dialect = Form.useWatch("dialect", form);

  useEffect(() => {
    const onResize = () => setIsMobile(window.innerWidth < 768);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRows(await ConnectionsApi.list());
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => {
    load();
  }, [load]);

  const persistView = (v: ViewMode) => {
    setView(v);
    localStorage.setItem(VIEW_KEY, v);
  };

  const filtered = useMemo(() => {
    const kw = keyword.trim().toLowerCase();
    if (!kw) return rows;
    return rows.filter(
      (r) =>
        r.name.toLowerCase().includes(kw) ||
        r.dialect.toLowerCase().includes(kw)
    );
  }, [rows, keyword]);

  const openCreate = () => {
    form.resetFields();
    form.setFieldsValue({
      dialect: "sqlite",
      db_path: "biz/myapp.db",
      host: "127.0.0.1",
      port: 3306,
      user: "root",
      create: true,
    });
    setOpen(true);
  };

  const onSubmit = async (values: FormValues) => {
    setSubmitting(true);
    try {
      await ConnectionsApi.create(
        values as unknown as Record<string, unknown>
      );
      message.success(t("connections.saveSuccess"));
      setOpen(false);
      form.resetFields();
      load();
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  const testConn = async (id: number) => {
    setTesting(id);
    try {
      const r = await ConnectionsApi.test(id);
      if (r.ok) message.success(t("connections.testSuccess"));
      else message.error(r.error || t("connections.testFail"));
      load();
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setTesting(null);
    }
  };

  const onDelete = async (c: Connection) => {
    try {
      await ConnectionsApi.remove(c.id);
      message.success(t("connections.deleteSuccess"));
      load();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const actionButtons = (c: Connection) => (
    <>
      <button
        type="button"
        className="dm-set-icon-btn"
        title={t("connections.test")}
        onClick={() => testConn(c.id)}
      >
        {testing === c.id ? (
          <Spin size="small" />
        ) : (
          <ApiOutlined />
        )}
      </button>
      <Popconfirm title={t("connections.deleteConfirm")} onConfirm={() => onDelete(c)}>
        <button
          type="button"
          className="dm-set-icon-btn dm-set-icon-btn-danger"
          title={t("connections.delete")}
        >
          <DeleteOutlined />
        </button>
      </Popconfirm>
    </>
  );

  const columns = [
    {
      key: "name",
      title: t("connections.colName"),
      dataIndex: "name",
      width: 260,
      fixed: isMobile ? undefined : ("left" as const),
      render: (_: unknown, c: Connection) => {
        const meta = statusMeta(c.status);
        return (
          <div className="dm-set-cell">
            <div className="dm-set-cell-avatar">
              {DIALECT_ICON[c.dialect]}
            </div>
            <div className="dm-set-cell-text">
              <span className="dm-set-cell-name">{c.name}</span>
              <span className="dm-set-cell-handle">
                {c.dialect.toUpperCase()}
              </span>
            </div>
            <span
              className="dm-set-status-dot"
              style={{ background: meta.tone }}
            />
          </div>
        );
      },
    },
    {
      key: "dialect",
      title: t("connections.colDialect"),
      dataIndex: "dialect",
      width: 140,
      render: (v: Dialect) => <span className="dm-set-pill">{v.toUpperCase()}</span>,
    },
    {
      key: "status",
      title: t("connections.colStatus"),
      dataIndex: "status",
      width: 140,
      render: (v: string) => {
        const meta = statusMeta(v);
        return (
          <span className="dm-set-pill">
            <span
              className="dm-set-status-dot"
              style={{ background: meta.tone }}
            />
            {t(meta.key)}
          </span>
        );
      },
    },
    {
      key: "actions",
      title: t("connections.colActions"),
      width: 140,
      fixed: isMobile ? undefined : ("right" as const),
      render: (_: unknown, c: Connection) => (
        <div className="dm-set-cell-actions">{actionButtons(c)}</div>
      ),
    },
  ];

  const renderCard = (c: Connection) => {
    const meta = statusMeta(c.status);
    return (
      <div className="dm-set-card" key={c.id}>
        <div className="dm-set-card-accent dm-set-card-accent-neutral" />
        <div className="dm-set-card-inner">
          <div className="dm-set-card-header">
            <div className="dm-set-card-avatar">
              {DIALECT_ICON[c.dialect]}
            </div>
            <div className="dm-set-card-title-block">
              <div className="dm-set-card-name-row">
                <span className="dm-set-card-name">{c.name}</span>
              </div>
              <span className="dm-set-card-handle">
                {c.dialect.toUpperCase()}
              </span>
            </div>
          </div>
          <div className="dm-set-card-meta">
            <span className="dm-set-pill">
              <span
                className="dm-set-status-dot"
                style={{ background: meta.tone }}
              />
              {t(meta.key)}
            </span>
          </div>
          <div className="dm-set-card-footer">
            {actionButtons(c)}
            <span className="dm-set-card-footer-spacer" />
            <span className="dm-set-id-badge">#{c.id}</span>
          </div>
        </div>
      </div>
    );
  };

  return (
    <>
      <div className="dm-set-page">
        <div className="dm-set-page-top">
          <div className="dm-set-legend" role="note">
            <span className="dm-set-legend-label">
              {t("connections.legendTitle")}
            </span>
            <span className="dm-set-legend-text">
              {t("connections.legend")}
            </span>
          </div>
          <div className="dm-set-grid-toolbar">
            <Input
              allowClear
              className="dm-set-search"
              prefix={<SearchOutlined />}
              placeholder={t("connections.searchPlaceholder")}
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
            />
            <div className="dm-set-grid-toolbar-right">
              <Segmented<ViewMode>
                size="small"
                value={view}
                onChange={persistView}
                options={[
                  {
                    value: "card",
                    icon: <AppstoreOutlined />,
                  },
                  {
                    value: "table",
                    icon: <UnorderedListOutlined />,
                  },
                ]}
              />
              <Button icon={<ReloadOutlined />} onClick={load}>
                {t("connections.refresh")}
              </Button>
              <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
                {t("connections.newConnection")}
              </Button>
            </div>
          </div>
        </div>

        {view === "card" ? (
          loading ? (
            <div className="dm-set-grid-loading">
              <Spin />
            </div>
          ) : filtered.length === 0 ? (
            <Empty
              description={
                rows.length === 0
                  ? t("connections.noConnections")
                  : t("connections.noMatch")
              }
            />
          ) : (
            <div className="dm-set-card-grid">
              {filtered.map(renderCard)}
            </div>
          )
        ) : (
          <ResizableTable
            storageKey={TABLE_WIDTH_KEY}
            rowKey="id"
            size="middle"
            className="dm-set-table"
            loading={loading}
            dataSource={filtered}
            pagination={false}
            scroll={{ x: 720 }}
            columns={columns}
          />
        )}
      </div>

      <Drawer
        title={t("connections.modalNewTitle")}
        open={open}
        onClose={() => setOpen(false)}
        width={
          typeof window !== "undefined"
            ? Math.min(520, window.innerWidth - 24)
            : 520
        }
        destroyOnHidden
        className="dm-set-drawer"
        styles={{ body: { paddingTop: 12, paddingBottom: 24 } }}
        footer={
          <div className="dm-set-drawer-footer">
            <Button onClick={() => setOpen(false)}>
              {t("connections.cancel")}
            </Button>
            <Button
              type="primary"
              loading={submitting}
              onClick={() => form.submit()}
            >
              {t("connections.save")}
            </Button>
          </div>
        }
      >
        <Form
          form={form}
          layout="vertical"
          requiredMark={false}
          onFinish={onSubmit}
          initialValues={{ dialect: "sqlite", create: true }}
          className="dm-set-form"
        >
          <div className="dm-set-section">
            <div className="dm-set-section-title">
              {t("connections.sectionBasic")}
            </div>
            <Form.Item
              label={t("connections.formName")}
              name="name"
              rules={[
                { required: true, message: t("connections.nameRequired") },
              ]}
            >
              <Input placeholder={t("connections.namePlaceholder")} />
            </Form.Item>
            <Form.Item label={t("connections.formDialect")} name="dialect">
              <Radio.Group
                optionType="button"
                buttonStyle="solid"
                options={[
                  { label: t("connections.sqliteHint"), value: "sqlite" },
                  { label: "MySQL", value: "mysql" },
                  { label: "PostgreSQL", value: "postgresql" },
                ]}
              />
            </Form.Item>
          </div>

          <div className="dm-set-section">
            <div className="dm-set-section-title">
              {t("connections.sectionConn")}
            </div>
            {dialect === "sqlite" ? (
              <>
                <Form.Item name="db_path" label={t("connections.formDbPath")}>
                  <Input placeholder="biz/myapp.db" />
                </Form.Item>
                <Form.Item name="create" label={t("connections.formCreate")}>
                  <Radio.Group
                    options={[
                      { label: t("connections.createYes"), value: true },
                      { label: t("connections.createNo"), value: false },
                    ]}
                  />
                </Form.Item>
              </>
            ) : (
              <>
                <div className="dm-set-form-row">
                  <Form.Item name="host" label={t("connections.formHost")}>
                    <Input placeholder="127.0.0.1" />
                  </Form.Item>
                  <Form.Item name="port" label={t("connections.formPort")}>
                    <InputNumber min={1} max={65535} style={{ width: "100%" }} />
                  </Form.Item>
                </div>
                <div className="dm-set-form-row dm-set-form-row-even">
                  <Form.Item name="user" label={t("connections.formUser")}>
                    <Input autoComplete="off" />
                  </Form.Item>
                  <Form.Item
                    name="password"
                    label={t("connections.formPassword")}
                  >
                    <Input.Password autoComplete="new-password" />
                  </Form.Item>
                </div>
                <Form.Item
                  name="database"
                  label={t("connections.formDatabase")}
                >
                  <Input />
                </Form.Item>
              </>
            )}
          </div>
        </Form>
      </Drawer>
    </>
  );
}
