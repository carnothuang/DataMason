import { useCallback, useEffect, useMemo, useState } from "react";
import {
  App,
  Button,
  Drawer,
  Empty,
  Form,
  Input,
  Modal,
  Popconfirm,
  Segmented,
  Space,
  Spin,
  Switch,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import {
  AppstoreOutlined,
  ClockCircleOutlined,
  DeleteOutlined,
  EditOutlined,
  KeyOutlined,
  LockOutlined,
  MailOutlined,
  PlusOutlined,
  ReloadOutlined,
  SearchOutlined,
  UnorderedListOutlined,
} from "@ant-design/icons";
import { useTranslation } from "react-i18next";
import dayjs from "dayjs";
import { AuthApi, CurrentUser, UserPayload } from "../../api";
import { ResizableTable } from "../../components/ResizableTable";
import "./UsersTab.css";

const { Text } = Typography;

const VIEW_KEY = "datamason:users:view";
const TABLE_WIDTH_KEY = "datamason:users:table-widths";
type ViewMode = "card" | "table";

interface CreateValues {
  username: string;
  display_name?: string;
  email?: string;
  password: string;
  confirm: string;
  is_platform_admin: boolean;
  is_active: boolean;
}

interface EditValues {
  display_name?: string;
  email?: string;
  is_platform_admin: boolean;
  is_active: boolean;
}

interface ResetValues {
  password: string;
  confirm: string;
}

function roleToneClass(isAdmin: boolean): string {
  return isAdmin
    ? "dm-users-role-tone-admin"
    : "dm-users-role-tone-user";
}

function userInitials(displayName: string, username: string): string {
  const source = displayName.trim() || username;
  const parts = source.split(/[\s._-]+/).filter(Boolean);
  if (parts.length >= 2) {
    return (parts[0][0] + parts[1][0]).toUpperCase();
  }
  return source.slice(0, 2).toUpperCase();
}

function getLockedRemainingMs(user: CurrentUser, nowMs: number): number {
  if (!user.locked_until) return 0;
  const until = dayjs(user.locked_until).valueOf();
  return Math.max(0, until - nowMs);
}

function PermissionSummary({ user }: { user: CurrentUser }) {
  const { t } = useTranslation();
  if (user.is_platform_admin) {
    return (
      <span className="dm-users-perm-badge dm-users-perm-badge-all">
        {t("users.permAll")}
      </span>
    );
  }
  return (
    <span className="dm-users-perm-badge">{t("users.permBasic")}</span>
  );
}

function RoleLegend() {
  const { t } = useTranslation();
  return (
    <div className="dm-users-role-legend" role="note">
      <span className="dm-users-role-legend-label">
        {t("users.roleLegendTitle")}
      </span>
      <p className="dm-users-role-legend-text">{t("users.roleLegend")}</p>
    </div>
  );
}

function UserLoginLock({
  user,
  nowMs,
  onUnlock,
}: {
  user: CurrentUser;
  nowMs: number;
  onUnlock: () => void;
}) {
  const { t } = useTranslation();
  const remaining = getLockedRemainingMs(user, nowMs);
  const failedCount = user.failed_login_count ?? 0;
  if (remaining <= 0) {
    if (failedCount > 0) {
      return (
        <Text type="secondary" style={{ fontSize: 12 }}>
          {t("users.loginFailedCount", { count: failedCount })}
        </Text>
      );
    }
    return (
      <Text type="secondary" style={{ fontSize: 12 }}>
        {t("users.loginLockNone")}
      </Text>
    );
  }
  const minutes = Math.max(1, Math.ceil(remaining / 60000));
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        flexWrap: "wrap",
      }}
    >
      <Tag
        color="error"
        style={{ margin: 0, fontSize: 11, lineHeight: "18px" }}
      >
        {t("users.loginLockActive", { minutes })}
      </Tag>
      <Button
        size="small"
        type="link"
        onClick={onUnlock}
        style={{ padding: 0, fontSize: 12, height: "auto" }}
      >
        {t("users.unlock")}
      </Button>
    </span>
  );
}

export default function UsersTab() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [rows, setRows] = useState<CurrentUser[]>([]);
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
  const [createOpen, setCreateOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [createForm] = Form.useForm<CreateValues>();
  const [editTarget, setEditTarget] = useState<CurrentUser | null>(null);
  const [editSubmitting, setEditSubmitting] = useState(false);
  const [editForm] = Form.useForm<EditValues>();
  const [resetTarget, setResetTarget] = useState<CurrentUser | null>(null);
  const [resetSubmitting, setResetSubmitting] = useState(false);
  const [resetForm] = Form.useForm<ResetValues>();
  const [currentId, setCurrentId] = useState<number | null>(null);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [isMobile, setIsMobile] = useState(
    () => typeof window !== "undefined" && window.innerWidth <= 768,
  );

  const hasLockedUser = useMemo(
    () => rows.some((u) => getLockedRemainingMs(u, nowMs) > 0),
    [rows, nowMs],
  );

  useEffect(() => {
    if (!hasLockedUser) return;
    const id = window.setInterval(() => setNowMs(Date.now()), 30000);
    return () => window.clearInterval(id);
  }, [hasLockedUser]);

  useEffect(() => {
    const onResize = () => setIsMobile(window.innerWidth <= 768);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRows(await AuthApi.listUsers());
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => {
    AuthApi.me()
      .then((me) => setCurrentId(me.id))
      .catch(() => undefined);
    void load();
  }, [load]);

  useEffect(() => {
    if (!hasLockedUser) return;
    const anyExpired = rows.some(
      (u) =>
        u.locked_until != null && getLockedRemainingMs(u, nowMs) === 0,
    );
    if (anyExpired) void load();
  }, [hasLockedUser, nowMs, rows, load]);

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
      (u) =>
        u.username.toLowerCase().includes(kw) ||
        (u.display_name || "").toLowerCase().includes(kw) ||
        (u.email || "").toLowerCase().includes(kw),
    );
  }, [rows, keyword]);

  const openCreate = () => {
    createForm.resetFields();
    createForm.setFieldsValue({
      is_platform_admin: false,
      is_active: true,
    });
    setCreateOpen(true);
  };

  const openEdit = (u: CurrentUser) => {
    setEditTarget(u);
    editForm.setFieldsValue({
      display_name: u.display_name === u.username ? "" : u.display_name,
      email: u.email,
      is_platform_admin: u.is_platform_admin,
      is_active: u.is_active,
    });
  };

  const onCreate = async (values: CreateValues) => {
    setSubmitting(true);
    const payload: UserPayload = {
      username: values.username,
      password: values.password,
      display_name: values.display_name,
      email: values.email,
      is_platform_admin: values.is_platform_admin,
      is_active: values.is_active,
    };
    try {
      await AuthApi.createUser(payload);
      message.success(t("users.createSuccess", { username: values.username }));
      createForm.resetFields();
      setCreateOpen(false);
      void load();
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  const onEditSubmit = async (values: EditValues) => {
    if (!editTarget) return;
    setEditSubmitting(true);
    const payload: UserPayload = {
      display_name: values.display_name,
      email: values.email,
      is_platform_admin: values.is_platform_admin,
      is_active: values.is_active,
    };
    try {
      await AuthApi.updateUser(editTarget.id, payload);
      message.success(t("users.saveSuccess"));
      setEditTarget(null);
      editForm.resetFields();
      void load();
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setEditSubmitting(false);
    }
  };

  const toggleActive = async (u: CurrentUser, active: boolean) => {
    try {
      await AuthApi.updateUser(u.id, { is_active: active });
      message.success(active ? t("users.enabledDone") : t("users.disabledDone"));
      void load();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const doUnlock = async (u: CurrentUser) => {
    try {
      await AuthApi.unlockUser(u.id);
      message.success(t("users.unlockSuccess"));
      void load();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const onDelete = async (u: CurrentUser) => {
    try {
      await AuthApi.deleteUser(u.id);
      message.success(t("users.deleteSuccess"));
      void load();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const onResetSubmit = async (values: ResetValues) => {
    if (!resetTarget) return;
    setResetSubmitting(true);
    try {
      await AuthApi.updateUser(resetTarget.id, { password: values.password });
      message.success(t("users.resetSuccess"));
      setResetTarget(null);
      resetForm.resetFields();
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setResetSubmitting(false);
    }
  };

  const columns = [
    {
      title: t("users.colUsername"),
      key: "username",
      width: 240,
      fixed: isMobile ? undefined : ("left" as const),
      render: (_: unknown, u: CurrentUser) => {
        const displayName = u.display_name?.trim() || u.username;
        return (
          <div className="dm-users-cell">
            <span
              className={`dm-users-cell-avatar ${roleToneClass(
                u.is_platform_admin,
              )}`}
            >
              {userInitials(displayName, u.username)}
            </span>
            <span className="dm-users-cell-text">
              <span className="dm-users-cell-name">
                {displayName}
                {currentId === u.id && (
                  <span className="dm-users-cell-you">{t("users.you")}</span>
                )}
              </span>
              <span className="dm-users-cell-handle">@{u.username}</span>
            </span>
          </div>
        );
      },
    },
    {
      title: t("users.colEmail"),
      key: "email",
      width: 180,
      ellipsis: true,
      render: (_: unknown, u: CurrentUser) => (
        <span className="dm-users-cell-muted">
          {u.email?.trim() || "—"}
        </span>
      ),
    },
    {
      title: t("users.colAuth"),
      key: "login_type",
      dataIndex: "login_type",
      width: 120,
      render: (v: string) => (
        <span className="dm-users-cell-muted">{v || "—"}</span>
      ),
    },
    {
      title: t("users.colRole"),
      key: "role",
      width: 96,
      render: (_: unknown, u: CurrentUser) => (
        <span
          className={`dm-users-card-pill ${roleToneClass(
            u.is_platform_admin,
          )}`}
        >
          {u.is_platform_admin ? t("users.roleAdmin") : t("users.roleUser")}
        </span>
      ),
    },
    {
      title: t("users.colPermissions"),
      key: "permissions",
      width: 130,
      render: (_: unknown, u: CurrentUser) => <PermissionSummary user={u} />,
    },
    {
      title: t("users.colEnabled"),
      key: "is_active",
      width: 80,
      render: (_: unknown, u: CurrentUser) => (
        <Switch
          size="small"
          checked={u.is_active}
          disabled={currentId === u.id}
          onChange={(checked) => toggleActive(u, checked)}
        />
      ),
    },
    {
      title: t("users.colCreatedAt"),
      key: "created_at",
      dataIndex: "created_at",
      width: 168,
      render: (v: string | null) => (
        <span className="dm-users-cell-muted">
          {v ? dayjs(v).format("YYYY/MM/DD HH:mm:ss") : "—"}
        </span>
      ),
    },
    {
      title: t("users.colLoginLock"),
      key: "login_lock",
      width: 180,
      render: (_: unknown, u: CurrentUser) => (
        <UserLoginLock
          user={u}
          nowMs={nowMs}
          onUnlock={() => void doUnlock(u)}
        />
      ),
    },
    {
      title: t("users.colActions"),
      key: "actions",
      width: 120,
      fixed: isMobile ? undefined : ("right" as const),
      render: (_: unknown, u: CurrentUser) => (
        <Space size={4}>
          <Tooltip title={t("users.edit")}>
            <button
              type="button"
              className="dm-users-icon-btn"
              onClick={() => openEdit(u)}
              aria-label={t("users.edit")}
            >
              <EditOutlined />
            </button>
          </Tooltip>
          <Tooltip title={t("users.resetPassword")}>
            <button
              type="button"
              className="dm-users-icon-btn"
              onClick={() => {
                setResetTarget(u);
                resetForm.resetFields();
              }}
              aria-label={t("users.resetPassword")}
            >
              <KeyOutlined />
            </button>
          </Tooltip>
          <Popconfirm
            title={t("users.deleteConfirm", { username: u.username })}
            onConfirm={() => void onDelete(u)}
            disabled={currentId === u.id}
          >
            <Tooltip
              title={
                currentId === u.id ? t("users.deleteSelf") : t("users.delete")
              }
            >
              <button
                type="button"
                className="dm-users-icon-btn dm-users-icon-btn-danger"
                disabled={currentId === u.id}
                aria-label={t("users.delete")}
              >
                <DeleteOutlined />
              </button>
            </Tooltip>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const renderCard = (u: CurrentUser) => {
    const isSelf = currentId === u.id;
    const displayName = u.display_name?.trim() || u.username;
    const remaining = getLockedRemainingMs(u, nowMs);
    const locked = remaining > 0;
    const failedCount = u.failed_login_count ?? 0;
    const accentClass = locked
      ? "dm-users-card-accent-locked"
      : !u.is_active
        ? "dm-users-card-accent-disabled"
        : u.is_platform_admin
          ? "dm-users-card-accent-admin"
          : "dm-users-card-accent-user";
    const statusColor = u.is_active ? "#52c41a" : "#8c8c8c";
    const statusBg = u.is_active
      ? "rgba(82,196,26,0.10)"
      : "rgba(140,140,140,0.10)";
    return (
      <div
        key={u.id}
        className={[
          "dm-users-card",
          locked ? "dm-users-card-locked" : "",
          !u.is_active ? "dm-users-card-disabled" : "",
        ]
          .filter(Boolean)
          .join(" ")}
      >
        <div className={`dm-users-card-accent ${accentClass}`} />
        <div className="dm-users-card-inner">
          <div className="dm-users-card-header">
            <div
              className={`dm-users-card-avatar ${roleToneClass(
                u.is_platform_admin,
              )}`}
              aria-hidden="true"
            >
              {userInitials(displayName, u.username)}
            </div>
            <div className="dm-users-card-title-block">
              <div className="dm-users-card-name-row">
                <span className="dm-users-card-name">{displayName}</span>
                {isSelf && (
                  <Tag className="dm-users-card-you-tag">{t("users.you")}</Tag>
                )}
              </div>
              <div className="dm-users-card-handle">@{u.username}</div>
            </div>
            <Switch
              size="small"
              checked={u.is_active}
              disabled={isSelf}
              onChange={(checked) => toggleActive(u, checked)}
              className="dm-users-card-switch"
              aria-label={t("users.colEnabled")}
            />
          </div>

          <div className="dm-users-card-meta">
            <span
              className={`dm-users-card-pill ${roleToneClass(
                u.is_platform_admin,
              )}`}
            >
              {u.is_platform_admin ? t("users.roleAdmin") : t("users.roleUser")}
            </span>
            <span
              className="dm-users-card-pill"
              style={{ color: statusColor, background: statusBg }}
            >
              <span
                className="dm-users-card-status-dot"
                style={{ background: statusColor }}
              />
              {u.is_active ? t("users.statusEnabled") : t("users.statusDisabled")}
            </span>
            <span className="dm-users-card-auth">{u.login_type}</span>
          </div>

          <div className="dm-users-card-info">
            <span className="dm-users-card-time">
              <MailOutlined />
              <span>{u.email?.trim() || "—"}</span>
            </span>
            {u.created_at != null && (
              <Tooltip title={t("users.colCreatedAt")}>
                <span className="dm-users-card-time">
                  <ClockCircleOutlined />
                  <span>{dayjs(u.created_at).format("YYYY/MM/DD HH:mm")}</span>
                </span>
              </Tooltip>
            )}
            <PermissionSummary user={u} />
          </div>

          {locked && (
            <div className="dm-users-card-lock-alert">
              <LockOutlined />
              <span className="dm-users-card-lock-text">
                {t("users.loginLockActive", {
                  minutes: Math.max(1, Math.ceil(remaining / 60000)),
                })}
              </span>
              <Button
                type="link"
                size="small"
                className="dm-users-card-lock-unlock"
                onClick={() => void doUnlock(u)}
              >
                {t("users.unlock")}
              </Button>
            </div>
          )}

          {!locked && failedCount > 0 && (
            <div className="dm-users-card-failed-hint">
              {t("users.loginFailedCount", { count: failedCount })}
            </div>
          )}

          <div className="dm-users-card-footer">
            <Tooltip title={t("users.edit")} mouseEnterDelay={0.5}>
              <button
                type="button"
                className="dm-users-icon-btn"
                onClick={() => openEdit(u)}
                aria-label={t("users.edit")}
              >
                <EditOutlined />
              </button>
            </Tooltip>
            <Tooltip title={t("users.resetPassword")} mouseEnterDelay={0.5}>
              <button
                type="button"
                className="dm-users-icon-btn"
                onClick={() => {
                  setResetTarget(u);
                  resetForm.resetFields();
                }}
                aria-label={t("users.resetPassword")}
              >
                <KeyOutlined />
              </button>
            </Tooltip>
            <Popconfirm
              title={t("users.deleteConfirm", { username: u.username })}
              onConfirm={() => void onDelete(u)}
              disabled={isSelf}
            >
              <Tooltip
                title={isSelf ? t("users.deleteSelf") : t("users.delete")}
                mouseEnterDelay={0.5}
              >
                <button
                  type="button"
                  className="dm-users-icon-btn dm-users-icon-btn-danger"
                  disabled={isSelf}
                  aria-label={t("users.delete")}
                >
                  <DeleteOutlined />
                </button>
              </Tooltip>
            </Popconfirm>
            <span className="dm-users-card-footer-spacer" />
            <span className="dm-users-card-id-badge">#{u.id}</span>
          </div>
        </div>
      </div>
    );
  };

  return (
    <>
      <div className="dm-users-page">
        <div className="dm-users-page-top">
          <RoleLegend />
          <div className="dm-users-grid-toolbar">
            <Input
              allowClear
              prefix={<SearchOutlined />}
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              placeholder={t("users.searchPlaceholder")}
              className="dm-users-search"
            />
            <div className="dm-users-grid-toolbar-right">
              <Segmented
                size="small"
                value={view}
                onChange={(v) => persistView(v as ViewMode)}
                options={[
                  {
                    value: "card",
                    label: (
                      <span className="dm-users-view-mode-label">
                        <AppstoreOutlined />
                        {t("users.viewCard")}
                      </span>
                    ),
                  },
                  {
                    value: "table",
                    label: (
                      <span className="dm-users-view-mode-label">
                        <UnorderedListOutlined />
                        {t("users.viewTable")}
                      </span>
                    ),
                  },
                ]}
              />
              <Button icon={<ReloadOutlined />} onClick={() => void load()}>
                {t("users.refresh")}
              </Button>
              <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
                {t("users.newUser")}
              </Button>
            </div>
          </div>
        </div>

        {view === "card" ? (
          loading && rows.length === 0 ? (
            <div className="dm-users-grid-loading">
              <Spin />
            </div>
          ) : filtered.length === 0 ? (
            <Empty
              description={
                rows.length === 0 ? t("users.noUsers") : t("users.noMatch")
              }
            />
          ) : (
            <div className="dm-users-card-grid">
              {filtered.map(renderCard)}
            </div>
          )
        ) : (
          <ResizableTable
            storageKey={TABLE_WIDTH_KEY}
            rowKey="id"
            size="middle"
            className="dm-users-table"
            loading={loading}
            dataSource={filtered}
            pagination={false}
            scroll={{ x: 1314 }}
            rowClassName={(u) =>
              [
                !u.is_active ? "dm-users-table-row-disabled" : "",
                getLockedRemainingMs(u, nowMs) > 0
                  ? "dm-users-table-row-locked"
                  : "",
              ]
                .filter(Boolean)
                .join(" ")
            }
            columns={columns}
          />
        )}
      </div>

      <Drawer
        title={t("users.modalNewTitle")}
        placement="right"
        open={createOpen}
        onClose={() => {
          setCreateOpen(false);
          createForm.resetFields();
        }}
        width={Math.min(
          520,
          typeof window !== "undefined" ? window.innerWidth - 24 : 520,
        )}
        destroyOnHidden
        className="dm-users-drawer"
        styles={{ body: { paddingTop: 12, paddingBottom: 24 } }}
        footer={
          <div className="dm-users-drawer-footer">
            <Button
              onClick={() => {
                setCreateOpen(false);
                createForm.resetFields();
              }}
            >
              {t("users.cancel")}
            </Button>
            <Button
              type="primary"
              loading={submitting}
              onClick={() => createForm.submit()}
            >
              {t("users.create")}
            </Button>
          </div>
        }
      >
        <Form<CreateValues>
          form={createForm}
          layout="vertical"
          requiredMark={false}
          onFinish={onCreate}
          initialValues={{ is_platform_admin: false, is_active: true }}
          className="dm-users-form"
        >
          <div className="dm-users-section">
            <div className="dm-users-section-title">
              {t("users.sectionAccount")}
            </div>
            <Form.Item
              label={t("users.formUsername")}
              name="username"
              rules={[
                { required: true, message: t("users.usernameRequired") },
                {
                  pattern: /^[a-zA-Z0-9_-]{1,64}$/,
                  message: t("users.usernameRule"),
                },
              ]}
            >
              <Input autoComplete="username" />
            </Form.Item>
            <Form.Item
              label={t("users.formDisplayName")}
              name="display_name"
            >
              <Input />
            </Form.Item>
            <Form.Item
              label={t("users.formEmail")}
              name="email"
              rules={[{ type: "email", message: t("users.emailInvalid") }]}
            >
              <Input type="email" autoComplete="email" />
            </Form.Item>
            <Form.Item
              label={t("users.formPassword")}
              name="password"
              rules={[
                { required: true, message: t("users.passwordRequired") },
                { min: 6, message: t("users.passwordMin") },
              ]}
            >
              <Input.Password autoComplete="new-password" />
            </Form.Item>
            <Form.Item
              label={t("users.formPasswordConfirm")}
              name="confirm"
              dependencies={["password"]}
              rules={[
                { required: true, message: t("users.passwordConfirmRequired") },
                ({ getFieldValue }) => ({
                  validator(_, value) {
                    if (!value || getFieldValue("password") === value) {
                      return Promise.resolve();
                    }
                    return Promise.reject(new Error(t("users.passwordMismatch")));
                  },
                }),
              ]}
            >
              <Input.Password autoComplete="new-password" />
            </Form.Item>
          </div>

          <div className="dm-users-section">
            <div className="dm-users-section-title">
              {t("users.sectionAccess")}
            </div>
            <Form.Item
              name="is_platform_admin"
              label={t("users.formAdmin")}
              valuePropName="checked"
            >
              <Switch />
            </Form.Item>
            <Form.Item
              name="is_active"
              label={t("users.formActive")}
              valuePropName="checked"
            >
              <Switch />
            </Form.Item>
          </div>
        </Form>
      </Drawer>

      <Drawer
        title={
          editTarget
            ? t("users.modalEditTitle", { username: editTarget.username })
            : t("users.edit")
        }
        placement="right"
        open={editTarget !== null}
        onClose={() => {
          setEditTarget(null);
          editForm.resetFields();
        }}
        width={Math.min(
          520,
          typeof window !== "undefined" ? window.innerWidth - 24 : 520,
        )}
        destroyOnHidden
        className="dm-users-drawer"
        styles={{ body: { paddingTop: 12, paddingBottom: 24 } }}
        footer={
          <div className="dm-users-drawer-footer">
            <Button
              onClick={() => {
                setEditTarget(null);
                editForm.resetFields();
              }}
            >
              {t("users.cancel")}
            </Button>
            <Button
              type="primary"
              loading={editSubmitting}
              onClick={() => editForm.submit()}
            >
              {t("users.save")}
            </Button>
          </div>
        }
      >
        <Form<EditValues>
          form={editForm}
          layout="vertical"
          requiredMark={false}
          onFinish={onEditSubmit}
          className="dm-users-form"
        >
          <div className="dm-users-section">
            <div className="dm-users-section-title">
              {t("users.sectionAccount")}
            </div>
            <Form.Item
              label={t("users.formDisplayName")}
              name="display_name"
            >
              <Input />
            </Form.Item>
            <Form.Item
              label={t("users.formEmail")}
              name="email"
              rules={[{ type: "email", message: t("users.emailInvalid") }]}
            >
              <Input type="email" autoComplete="email" />
            </Form.Item>
          </div>

          <div className="dm-users-section">
            <div className="dm-users-section-title">
              {t("users.sectionAccess")}
            </div>
            <Form.Item
              name="is_platform_admin"
              label={t("users.formAdmin")}
              valuePropName="checked"
            >
              <Switch />
            </Form.Item>
            <Form.Item
              name="is_active"
              label={t("users.formActive")}
              valuePropName="checked"
            >
              <Switch />
            </Form.Item>
          </div>
        </Form>
      </Drawer>

      <Modal
        title={
          resetTarget
            ? t("users.modalResetTitle", { username: resetTarget.username })
            : ""
        }
        open={resetTarget !== null}
        onCancel={() => {
          setResetTarget(null);
          resetForm.resetFields();
        }}
        onOk={() => resetForm.submit()}
        okText={t("users.reset")}
        cancelText={t("users.cancel")}
        confirmLoading={resetSubmitting}
      >
        <Text type="secondary" style={{ display: "block", marginBottom: 8 }}>
          {t("users.resetHint")}
        </Text>
        <Form<ResetValues>
          form={resetForm}
          layout="vertical"
          onFinish={onResetSubmit}
        >
          <Form.Item
            label={t("users.newPassword")}
            name="password"
            rules={[
              { required: true, message: t("users.newPasswordRequired") },
              { min: 6, message: t("users.passwordMin") },
            ]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          <Form.Item
            label={t("users.formPasswordConfirm")}
            name="confirm"
            dependencies={["password"]}
            rules={[
              { required: true, message: t("users.passwordConfirmRequired") },
              ({ getFieldValue }) => ({
                validator(_, value) {
                  if (!value || getFieldValue("password") === value) {
                    return Promise.resolve();
                  }
                  return Promise.reject(new Error(t("users.passwordMismatch")));
                },
              }),
            ]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
