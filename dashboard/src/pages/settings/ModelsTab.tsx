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
  Segmented,
  Spin,
  Tooltip,
} from "antd";
import {
  ApiOutlined,
  AppstoreOutlined,
  DeleteOutlined,
  EditOutlined,
  LinkOutlined,
  PlusOutlined,
  ReloadOutlined,
  RobotOutlined,
  SearchOutlined,
  StarOutlined,
  UnorderedListOutlined,
} from "@ant-design/icons";
import { useTranslation } from "react-i18next";
import { LlmModel, ModelsApi } from "../../api";
import { ResizableTable } from "../../components/ResizableTable";
import "./shared.css";

const VIEW_KEY = "datamason:models:view";
const TABLE_WIDTH_KEY = "datamason:models:table-widths";
type ViewMode = "card" | "table";

interface FormValues {
  name: string;
  base_url: string;
  model_name: string;
  temperature: number;
  api_key: string;
}

export default function ModelsTab() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [rows, setRows] = useState<LlmModel[]>([]);
  const [loading, setLoading] = useState(true);
  const [keyword, setKeyword] = useState("");
  const [view, setView] = useState<ViewMode>(() => {
    try {
      const saved = localStorage.getItem(VIEW_KEY);
      if (saved === "card" || saved === "table") return saved;
    } catch {
      // ignore
    }
    return "table";
  });
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<LlmModel | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [testing, setTesting] = useState<number | null>(null);
  const [form] = Form.useForm<FormValues>();
  const [isMobile, setIsMobile] = useState(
    () => typeof window !== "undefined" && window.innerWidth <= 768,
  );

  useEffect(() => {
    const onResize = () => setIsMobile(window.innerWidth <= 768);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRows(await ModelsApi.list());
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => {
    void load();
  }, [load]);

  const persistView = (next: ViewMode) => {
    setView(next);
    try {
      localStorage.setItem(VIEW_KEY, next);
    } catch {
      // ignore
    }
  };

  const filtered = useMemo(() => {
    const kw = keyword.trim().toLowerCase();
    if (!kw) return rows;
    return rows.filter(
      (m) =>
        m.name.toLowerCase().includes(kw) ||
        m.model_name.toLowerCase().includes(kw) ||
        m.base_url.toLowerCase().includes(kw),
    );
  }, [rows, keyword]);

  const openCreate = () => {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({ temperature: 0 });
    setOpen(true);
  };

  const openEdit = (m: LlmModel) => {
    setEditing(m);
    form.setFieldsValue({
      name: m.name,
      base_url: m.base_url,
      model_name: m.model_name,
      temperature: m.temperature,
      api_key: m.has_api_key ? "********" : "",
    });
    setOpen(true);
  };

  const onSubmit = async (values: FormValues) => {
    setSubmitting(true);
    try {
      if (editing) {
        await ModelsApi.update(editing.id, values);
        message.success(t("models.saveSuccess"));
      } else {
        await ModelsApi.create(values);
        message.success(t("models.createSuccess", { name: values.name }));
      }
      setOpen(false);
      form.resetFields();
      void load();
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  const testModel = async (id: number) => {
    setTesting(id);
    try {
      const r = await ModelsApi.test(id);
      if (r.ok) message.success(t("models.testSuccess"));
      else message.error(r.error || t("models.testFail"));
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setTesting(null);
    }
  };

  const setDefault = async (m: LlmModel) => {
    try {
      await ModelsApi.setDefault(m.id);
      message.success(t("models.setDefaultDone"));
      void load();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const onDelete = async (m: LlmModel) => {
    try {
      await ModelsApi.remove(m.id);
      message.success(t("models.deleteSuccess"));
      void load();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const actionButtons = (m: LlmModel) => (
    <>
      <Tooltip title={t("models.edit")}>
        <button
          type="button"
          className="dm-set-icon-btn"
          onClick={() => openEdit(m)}
          aria-label={t("models.edit")}
        >
          <EditOutlined />
        </button>
      </Tooltip>
      <Tooltip title={t("models.test")}>
        <button
          type="button"
          className="dm-set-icon-btn"
          onClick={() => void testModel(m.id)}
          disabled={testing === m.id}
          aria-label={t("models.test")}
        >
          {testing === m.id ? <Spin size="small" /> : <ApiOutlined />}
        </button>
      </Tooltip>
      {!m.is_default && (
        <Tooltip title={t("models.setDefault")}>
          <button
            type="button"
            className="dm-set-icon-btn"
            onClick={() => void setDefault(m)}
            aria-label={t("models.setDefault")}
          >
            <StarOutlined />
          </button>
        </Tooltip>
      )}
      {!m.is_default && (
        <Popconfirm
          title={t("models.deleteConfirm", { name: m.name })}
          onConfirm={() => void onDelete(m)}
        >
          <Tooltip title={t("models.delete")}>
            <button
              type="button"
              className="dm-set-icon-btn dm-set-icon-btn-danger"
              aria-label={t("models.delete")}
            >
              <DeleteOutlined />
            </button>
          </Tooltip>
        </Popconfirm>
      )}
    </>
  );

  const columns = [
    {
      title: t("models.colName"),
      key: "name",
      width: 220,
      fixed: isMobile ? undefined : ("left" as const),
      render: (_: unknown, m: LlmModel) => (
        <div className="dm-set-cell">
          <span className="dm-set-cell-avatar">
            <RobotOutlined />
          </span>
          <span className="dm-set-cell-text">
            <span className="dm-set-cell-name">
              {m.name}
              {m.is_default && (
                <span className="dm-set-cell-tag">{t("models.defaultTag")}</span>
              )}
            </span>
            <span className="dm-set-cell-handle">{m.model_name}</span>
          </span>
        </div>
      ),
    },
    {
      title: t("models.colEndpoint"),
      key: "base_url",
      width: 260,
      ellipsis: true,
      render: (_: unknown, m: LlmModel) => (
        <span className="dm-set-cell-muted">{m.base_url || "—"}</span>
      ),
    },
    {
      title: t("models.colKey"),
      key: "has_api_key",
      width: 110,
      render: (_: unknown, m: LlmModel) => (
        <span className="dm-set-pill">
          <span
            className="dm-set-status-dot"
            style={{ background: m.has_api_key ? "#52c41a" : "#bfbfbf" }}
          />
          {m.has_api_key ? t("models.keySet") : t("models.keyUnset")}
        </span>
      ),
    },
    {
      title: t("models.colTemperature"),
      key: "temperature",
      dataIndex: "temperature",
      width: 90,
      render: (v: number) => <span className="dm-set-cell-muted">{v}</span>,
    },
    {
      title: t("models.colActions"),
      key: "actions",
      width: 190,
      fixed: isMobile ? undefined : ("right" as const),
      render: (_: unknown, m: LlmModel) => actionButtons(m),
    },
  ];

  const renderCard = (m: LlmModel) => (
    <div key={m.id} className="dm-set-card">
      <div
        className={`dm-set-card-accent ${
          m.is_default
            ? "dm-set-card-accent-brand"
            : "dm-set-card-accent-neutral"
        }`}
      />
      <div className="dm-set-card-inner">
        <div className="dm-set-card-header">
          <div className="dm-set-card-avatar" aria-hidden="true">
            <RobotOutlined />
          </div>
          <div className="dm-set-card-title-block">
            <div className="dm-set-card-name-row">
              <span className="dm-set-card-name">{m.name}</span>
              {m.is_default && (
                <span className="dm-set-card-tag">{t("models.defaultTag")}</span>
              )}
            </div>
            <div className="dm-set-card-handle">{m.model_name}</div>
          </div>
        </div>

        <div className="dm-set-card-meta">
          <span className="dm-set-pill">
            <span
              className="dm-set-status-dot"
              style={{ background: m.has_api_key ? "#52c41a" : "#bfbfbf" }}
            />
            {m.has_api_key ? t("models.keySet") : t("models.keyUnset")}
          </span>
          <span className="dm-set-pill">
            {t("models.colTemperature")}: {m.temperature}
          </span>
        </div>

        <div className="dm-set-card-info">
          <span className="dm-set-card-time">
            <LinkOutlined />
            <span
              style={{
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}
            >
              {m.base_url || "—"}
            </span>
          </span>
        </div>

        <div className="dm-set-card-footer">
          {actionButtons(m)}
          <span className="dm-set-card-footer-spacer" />
          <span className="dm-set-id-badge">#{m.id}</span>
        </div>
      </div>
    </div>
  );

  return (
    <>
      <div className="dm-set-page">
        <div className="dm-set-page-top">
          <div className="dm-set-legend" role="note">
            <span className="dm-set-legend-label">
              {t("models.legendTitle")}
            </span>
            <p className="dm-set-legend-text">{t("models.legend")}</p>
          </div>
          <div className="dm-set-grid-toolbar">
            <Input
              allowClear
              prefix={<SearchOutlined />}
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              placeholder={t("models.searchPlaceholder")}
              className="dm-set-search"
            />
            <div className="dm-set-grid-toolbar-right">
              <Segmented
                size="small"
                value={view}
                onChange={(v) => persistView(v as ViewMode)}
                options={[
                  {
                    value: "card",
                    label: (
                      <span className="dm-set-view-mode-label">
                        <AppstoreOutlined />
                        {t("models.viewCard")}
                      </span>
                    ),
                  },
                  {
                    value: "table",
                    label: (
                      <span className="dm-set-view-mode-label">
                        <UnorderedListOutlined />
                        {t("models.viewTable")}
                      </span>
                    ),
                  },
                ]}
              />
              <Button icon={<ReloadOutlined />} onClick={() => void load()}>
                {t("models.refresh")}
              </Button>
              <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
                {t("models.newModel")}
              </Button>
            </div>
          </div>
        </div>

        {view === "card" ? (
          loading && rows.length === 0 ? (
            <div className="dm-set-grid-loading">
              <Spin />
            </div>
          ) : filtered.length === 0 ? (
            <Empty
              description={
                rows.length === 0 ? t("models.noModels") : t("models.noMatch")
              }
            />
          ) : (
            <div className="dm-set-card-grid">{filtered.map(renderCard)}</div>
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
            scroll={{ x: 900 }}
            columns={columns}
          />
        )}
      </div>

      <Drawer
        title={
          editing
            ? t("models.modalEditTitle", { name: editing.name })
            : t("models.modalNewTitle")
        }
        placement="right"
        open={open}
        onClose={() => {
          setOpen(false);
          form.resetFields();
        }}
        width={Math.min(
          520,
          typeof window !== "undefined" ? window.innerWidth - 24 : 520,
        )}
        destroyOnHidden
        className="dm-set-drawer"
        styles={{ body: { paddingTop: 12, paddingBottom: 24 } }}
        footer={
          <div className="dm-set-drawer-footer">
            <Button
              onClick={() => {
                setOpen(false);
                form.resetFields();
              }}
            >
              {t("models.cancel")}
            </Button>
            <Button
              type="primary"
              loading={submitting}
              onClick={() => form.submit()}
            >
              {editing ? t("models.save") : t("models.create")}
            </Button>
          </div>
        }
      >
        <Form<FormValues>
          form={form}
          layout="vertical"
          requiredMark={false}
          onFinish={onSubmit}
          initialValues={{ temperature: 0 }}
          className="dm-set-form"
        >
          <div className="dm-set-section">
            <div className="dm-set-section-title">
              {t("models.sectionBasic")}
            </div>
            <Form.Item
              label={t("models.formName")}
              name="name"
              rules={[{ required: true, message: t("models.nameRequired") }]}
            >
              <Input />
            </Form.Item>
            <Form.Item
              label={t("models.formBaseUrl")}
              name="base_url"
              rules={[
                { required: true, message: t("models.baseUrlRequired") },
              ]}
            >
              <Input placeholder="https://api.openai.com/v1" />
            </Form.Item>
            <Form.Item
              label={t("models.formModelName")}
              name="model_name"
              rules={[
                { required: true, message: t("models.modelNameRequired") },
              ]}
            >
              <Input placeholder="gpt-4o" />
            </Form.Item>
            <Form.Item label={t("models.formTemperature")} name="temperature">
              <InputNumber min={0} max={2} step={0.1} style={{ width: 160 }} />
            </Form.Item>
          </div>

          <div className="dm-set-section">
            <div className="dm-set-section-title">
              {t("models.sectionKey")}
            </div>
            <Form.Item
              label={t("models.formApiKey")}
              name="api_key"
              rules={
                editing
                  ? []
                  : [
                      {
                        required: true,
                        message: t("models.apiKeyRequiredNew"),
                      },
                    ]
              }
              extra={editing ? t("models.apiKeyEditHint") : undefined}
            >
              <Input.Password autoComplete="new-password" />
            </Form.Item>
          </div>
        </Form>
      </Drawer>
    </>
  );
}
