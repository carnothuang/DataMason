import axios from "axios";

// 开发环境经 vite proxy 转发到 Flask；生产同源
export const http = axios.create({ baseURL: "/api/v1", timeout: 30000 });

// 401 全局拦截：会话失效 → 清缓存并跳登录页（仅浏览器侧；多次跳转去重）
let redirectingTo = "";
http.interceptors.response.use(
  (resp) => resp,
  (error) => {
    const status = error?.response?.status;
    if (status === 401 && typeof window !== "undefined") {
      // M3-5：访客公开页匿名访问，me() 必然 401——不在此处强制跳登录
      if (window.location.pathname.startsWith("/public/")) {
        return Promise.reject(new Error(error?.response?.data?.error || "请求失败"));
      }
      const target = "/login";
      if (redirectingTo !== target) {
        redirectingTo = target;
        // 清掉本地缓存的当前用户，避免门卫误判
        try {
          sessionStorage.removeItem("datamason_me");
        } catch {
          /* ignore */
        }
        // 用 location 强制刷新状态，避免 React 树内 401 反复重试
        if (window.location.pathname !== target) {
          window.location.href = target;
        }
      }
    }
    const msg =
      error?.code === "ECONNABORTED"
        ? "后端响应超时：服务可能仍在处理或已无响应，请确认后端状态后重试"
        : error?.response?.data?.error ||
          error?.message ||
          "请求失败";
    return Promise.reject(new Error(msg));
  },
);

// ---------- 类型 ----------
export interface LlmModel {
  id: number;
  name: string;
  base_url: string;
  model_name: string;
  temperature: number;
  is_default: boolean;
  has_api_key: boolean;
  api_key: string;
}

export interface Connection {
  id: number;
  name: string;
  dialect: "sqlite" | "mysql" | "postgresql";
  status: string;
}

export interface RedisConfig {
  enabled: boolean;
  host: string;
  port: number;
  db: number;
  password: string;
}

export interface Deployment {
  id: number;
  biz_name: string;
  purpose: string;
  status: string;
  entry_path: string;
  data_mode: "create" | "link";
  seed_data: boolean;
  role?: string;
  created_at: string;
}

export interface ProposalTable {
  name: string;
  biz_name: string;
  columns: { name: string; biz_name?: string; type: string; is_pk?: boolean; is_fk?: boolean; ref_table?: string | null }[];
}

export interface ProposalPage {
  table: string;
  title: string;
  kind: "crud_list" | "public_form" | "dashboard";
  access: "member" | "public";
}

export interface Proposal {
  biz_notes: string;
  attempts: number;
  spec_json: Record<string, unknown>;
  tables: ProposalTable[];
  pages: ProposalPage[];
}

export interface DeployResult {
  deployment_id: number;
  biz_name: string;
  status: string;
  entry_path: string;
  table_count: number;
  seeded: boolean;
  data_mode: "create" | "link";
}

// ---------- 直连模式（M2-7，接已有的） ----------
export interface LinkTableInventory {
  name: string;
  columns: string[];
  column_count: number;
  pk: string | null;
  single_pk: boolean;
  has_created_by: boolean;
  ownership_columns: string[];
  fk_targets: string[];
}

export interface LinkSelection {
  triggered: boolean;
  trigger_threshold: number;
  max_tables: number;
  total: number;
  selection: string[];
  no_pk: string[];
}

export interface LinkProposal extends Proposal {
  data_mode: "link";
  connection_id: number;
  link: {
    selected_tables: string[];
    no_pk_tables: string[];
    missing_tables: string[];
    dropped_fks: {
      table: string;
      column: string;
      ref_table: string;
      ref_column: string;
      reason: string;
    }[];
    ownership_missing: string[];
    enrich_failed?: string;
  };
}

// ---------- 工作台运行时（M2-2） ----------
export interface TableInfo {
  name: string;
  biz_name: string;
  columns: string[];
}

export interface RowPage {
  data: Record<string, unknown>[];
  page: number;
  page_size: number;
  total: number;
  columns: string[];
}

export interface FieldColumn {
  name: string;
  biz_name: string;
  type: string;
  length: number | null;
  nullable: boolean;
  is_pk: boolean;
  is_fk: boolean;
  ref_table: string | null;
  is_ownership: boolean;
}

export interface TableBlueprint {
  name: string;
  biz_name: string;
  columns: FieldColumn[];
}

// ---------- M2-8 数据蓝图：结构变更 diff 提案 ----------
export interface StructureDiff {
  action: "add" | "drop" | "rename" | "change_type";
  name: string;
  old?: string;
  biz_name?: string;
  type?: string;
  length?: number | null;
}

export interface StructureProposal {
  table: string;
  dirty: boolean;
  diff: StructureDiff[];
  statements: string[];
  final_columns: FieldColumn[];
}

// ---------- 工作台运行时（M2-2） ----------
export interface WorkspaceMenu {
  id: number;
  label: string;
  kind: string;
  ref_id: number | null;
  children: WorkspaceMenu[];
}

export interface WorkspacePage {
  id: number;
  kind: string;
  access: string;
  title: string;
  flow_id: number | null;
  table: string | null;
  table_biz_name: string | null;
}

export interface WorkspaceInfo {
  deployment_id: number;
  biz_name: string;
  status: string;
  entry_path: string;
  data_mode: "create" | "link";
  role: "creator" | "admin" | "operator";
  menus: WorkspaceMenu[];
  pages: WorkspacePage[];
}

// ---------- 成员与权限（M2-5） ----------
export interface DeploymentMember {
  user_id: number;
  username: string;
  display_name: string;
  role: "creator" | "admin" | "operator";
  menu_ids: number[];
  is_self: boolean;
}

export interface MemberMenusPayload {
  members: DeploymentMember[];
  menus: { id: number; label: string; parent_id: number | null }[];
}

export const MembersApi = {
  list: (deploymentId: number) =>
    http
      .get<MemberMenusPayload>(`/deployments/${deploymentId}/members`)
      .then((r) => r.data),
  candidates: (deploymentId: number) =>
    http
      .get<CurrentUser[]>(`/deployments/${deploymentId}/members/candidates`)
      .then((r) => ensureArray<CurrentUser>(r.data, "候选成员")),
  add: (
    deploymentId: number,
    payload: { username: string; role: "admin" | "operator"; menu_ids?: number[] },
  ) =>
    http
      .post<{ ok: boolean }>(`/deployments/${deploymentId}/members`, payload)
      .then((r) => r.data),
  update: (
    deploymentId: number,
    userId: number,
    payload: { role?: "admin" | "operator"; menu_ids?: number[] },
  ) =>
    http
      .put<{ ok: boolean }>(`/deployments/${deploymentId}/members/${userId}`, payload)
      .then((r) => r.data),
  remove: (deploymentId: number, userId: number) =>
    http
      .delete<{ ok: boolean }>(`/deployments/${deploymentId}/members/${userId}`)
      .then((r) => r.data),
};

// ---------- CRUD 元数据（M2-3/M2-4） ----------
export interface FkRef {
  ref_table: string;
  ref_column: string;
}

export interface MetaColumn {
  name: string;
  biz_name: string;
  type: string;
  length: number | null;
  nullable: boolean;
  pk: boolean;
  unique: boolean;
  fk: FkRef | null;
  is_ownership: boolean;
}

export interface TableMeta {
  columns: MetaColumn[];
}

export interface RefOption {
  value: number | string;
  label: string;
}

export interface RefPage {
  ref_table: string;
  ref_column: string;
  label_column: string | null;
  options: RefOption[];
  page: number;
  page_size: number;
  total: number;
}

export interface CrudRowFilters {
  page?: number;
  page_size?: number;
  sort?: string;
  order?: string;
  search?: string;
  [col: string]: string | number | undefined;
}

// ---------- 工作台运行时（M2-2） ----------
export const WorkspaceApi = {
  info: (deploymentId: number) =>
    http.get<WorkspaceInfo>(`/app/${deploymentId}`).then((r) => r.data),
};

// ---------- 工作台业务 CRUD（M2-3/M2-4，挂 /app/{id}/api/） ----------
export const CrudApi = {
  meta: (deploymentId: number) =>
    http.get<Record<string, TableMeta>>(`/app/${deploymentId}/api/meta`).then((r) => r.data),
  rows: (deploymentId: number, table: string, params: CrudRowFilters) =>
    http
      .get<RowPage>(`/app/${deploymentId}/api/tables/${encodeURIComponent(table)}/rows`, { params })
      .then((r) => r.data),
  get: (deploymentId: number, table: string, pk: string | number) =>
    http
      .get<Record<string, unknown>>(
        `/app/${deploymentId}/api/tables/${encodeURIComponent(table)}/rows/${pk}`,
      )
      .then((r) => r.data),
  create: (deploymentId: number, table: string, values: Record<string, unknown>) =>
    http
      .post<{ id: string | number }>(
        `/app/${deploymentId}/api/tables/${encodeURIComponent(table)}/rows`,
        values,
      )
      .then((r) => r.data),
  update: (
    deploymentId: number,
    table: string,
    pk: string | number,
    values: Record<string, unknown>,
  ) =>
    http
      .put<{ updated: boolean }>(
        `/app/${deploymentId}/api/tables/${encodeURIComponent(table)}/rows/${pk}`,
        values,
      )
      .then((r) => r.data),
  remove: (deploymentId: number, table: string, pk: string | number) =>
    http
      .delete<{ deleted: boolean }>(
        `/app/${deploymentId}/api/tables/${encodeURIComponent(table)}/rows/${pk}`,
      )
      .then((r) => r.data),
  refs: (
    deploymentId: number,
    table: string,
    fkCol: string,
    params: { search?: string; page?: number; page_size?: number },
  ) =>
    http
      .get<RefPage>(
        `/app/${deploymentId}/api/tables/${encodeURIComponent(table)}/refs/${encodeURIComponent(fkCol)}`,
        { params },
      )
      .then((r) => r.data),
};

// ---------- 操作日志（M2-6） ----------
export interface ActivityLog {
  id: number;
  occurred_at: string;
  actor_user_id: number | null;
  actor_display: string;
  action: "create" | "update" | "delete" | "deploy" | "member" | "structure" | "flow";
  object_kind: string;
  object_table: string;
  object_pk: string;
  object_label: string;
  object_biz_name: string;
  changes: Record<string, { old: unknown; new: unknown }>;
}

export interface LogsPage {
  logs: ActivityLog[];
  page: number;
  page_size: number;
  total: number;
}

export interface LogFilters {
  action?: string;
  actor_user_id?: number;
  object_table?: string;
  object_pk?: string;
  from?: string;
  to?: string;
  page?: number;
  page_size?: number;
}

export const LogsApi = {
  list: (deploymentId: number, params: LogFilters = {}) =>
    http
      .get<LogsPage>(`/app/${deploymentId}/api/activity-logs`, { params })
      .then((r) => r.data),
};

// ---------- M3-2 界面与菜单（工程室，创建人独有 DR-14） ----------
export interface EngineerMenuItem {
  id: number;
  label: string;
  kind: string;
  ref_id: number | null;
  parent_id: number | null;
}

export interface EngineerPageCard {
  id: number;
  kind: string;
  access: string;
  title: string;
  flow_id: number | null;
  table: string | null;
  table_biz_name: string | null;
}

export interface EngineerInterfacesPayload {
  menus: EngineerMenuItem[];
  pages: EngineerPageCard[];
  granted_menu_ids: number[];
  entry_path: string;
}

export interface EntryLookupResult {
  found: boolean;
  deployment_id?: number;
  entry_path?: string;
  status?: string;
  biz_name?: string;
  role?: string;
}

export const EngineerApi = {
  /** 工程室「界面与菜单」数据：菜单（平铺）+ 页面卡 + 已授权菜单 id + 入口路径。 */
  interfaces: (deploymentId: number) =>
    http
      .get<EngineerInterfacesPayload>(`/deployments/${deploymentId}/interfaces`)
      .then((r) => r.data),
  /** 整组保存菜单树（即拖即存）；nodes 按菜单树前序拍平，父引用 parent_id（-1/-2… 指列表位置）。 */
  saveMenus: (
    deploymentId: number,
    nodes: Array<{
      id?: number | null;
      label: string;
      kind?: string;
      ref_id?: number | null;
      parent_id?: number | null;
    }>,
  ) =>
    http
      .put<{ ok: boolean; nodes: EngineerMenuItem[] }>(
        `/deployments/${deploymentId}/menus`,
        { nodes },
      )
      .then((r) => r.data),
  /** 界面卡「调整」：改页面标题（页面编辑器整体在后续里程碑）。 */
  updatePageTitle: (deploymentId: number, pageId: number, title: string) =>
    http
      .put<{ ok: boolean }>(`/deployments/${deploymentId}/pages/${pageId}`, { title })
      .then((r) => r.data),
  /** 自定义入口路径（DR-15）：留空恢复到 /app/{id}。 */
  setEntryPath: (deploymentId: number, path: string) =>
    http
      .put<{ deployment_id: number; entry_path: string }>(
        `/deployments/${deploymentId}/entry_path`,
        { path },
      )
      .then((r) => r.data),
};

export const EntryApi = {
  /** 自定义入口解析：path=精确匹配 entry_path（成员才返回，防泄露）。 */
  lookup: (path: string) =>
    http
      .get<EntryLookupResult>("/entry/lookup", { params: { path } })
      .then((r) => r.data),
};

// ---------- M3-4 流程向导 v0（DR-06，向导式流程非工作流引擎） ----------
export interface FlowStep {
  index: number;
  name: string;
  page_id: number | null;
  page_title: string;
  kind: string;
  table: string | null;
  table_biz_name: string | null;
  done_when: { type: string; min: number };
  status_field: string | null;
  status_value: string | null;
}

export interface FlowSpec {
  id: number;
  name: string;
  version: number;
  /** 流程页 page_specs.id（菜单 kind=flow 的 ref_id 指向它）。 */
  page_id: number | null;
  steps: FlowStep[];
}

export interface FlowRuntimeStep extends FlowStep {
  condition: string;
  state: "done" | "current" | "pending";
  met: boolean;
}

export interface FlowRuntime {
  id: number;
  name: string;
  version: number;
  done: boolean;
  current_index: number;
  page_id: number | null;
  steps: FlowRuntimeStep[];
}

export interface FlowStepInput {
  name: string;
  page_id: number;
  done_when?: { type: string; min: number };
  status_field?: string | null;
  status_value?: string | null;
}

export const FlowApi = {
  /** 流程清单（工程室，创建人独有 DR-14）。 */
  list: (deploymentId: number) =>
    http
      .get<{ flows: FlowSpec[] }>(`/deployments/${deploymentId}/flows`)
      .then((r) => r.data.flows),
  create: (deploymentId: number, payload: { name: string; steps: FlowStepInput[] }) =>
    http
      .post<FlowSpec>(`/deployments/${deploymentId}/flows`, payload)
      .then((r) => r.data),
  update: (deploymentId: number, flowId: number, payload: { name: string; steps: FlowStepInput[] }) =>
    http
      .put<FlowSpec>(`/deployments/${deploymentId}/flows/${flowId}`, payload)
      .then((r) => r.data),
  remove: (deploymentId: number, flowId: number) =>
    http
      .delete<{ ok: boolean }>(`/deployments/${deploymentId}/flows/${flowId}`)
      .then((r) => r.data),
};

export const FlowRuntimeApi = {
  /** 运行态：游标推导 已完成/当前/待做 + 当前步完成条件。 */
  get: (deploymentId: number, flowId: number) =>
    http
      .get<FlowRuntime>(`/app/${deploymentId}/flows/${flowId}`)
      .then((r) => r.data),
  /** 下一步：完成条件不满足 → 409（error 提示）；满足则推进状态字段并写日志。 */
  advance: (deploymentId: number, flowId: number) =>
    http
      .post<{ ok: boolean; step: string; current_index: number; done: boolean; records_advanced: number }>(
        `/app/${deploymentId}/flows/${flowId}/advance`,
      )
      .then((r) => r.data),
};

// ---------- M3-5 访客公开页 + 分享链接（DR-04） ----------
export interface PublicField {
  name: string;
  biz_name: string;
  type: string;
  length: number | null;
  nullable: boolean;
  is_pk: boolean;
  is_ownership: boolean;
}

export interface PublicPageMeta {
  deployment_id: number;
  biz_name: string;
  page_id: number;
  title: string;
  kind: string;
  table: string;
  fields: PublicField[];
}

export const PublicApi = {
  /** 匿名读取公开表单元数据（访客仅能提交，看不到菜单与数据）。 */
  page: (deploymentId: number, pageId: number) =>
    http
      .get<PublicPageMeta>(`/public/${deploymentId}/pages/${pageId}`)
      .then((r) => r.data),
  /** 匿名提交到公开表单（走 CRUD 写路径，归属记系统，进操作日志）。 */
  submit: (deploymentId: number, pageId: number, values: Record<string, unknown>) =>
    http
      .post<{ ok: boolean; id: string | number }>(
        `/public/${deploymentId}/pages/${pageId}/submit`,
        values,
      )
      .then((r) => r.data),
};

export interface ShareLink {
  id: number;
  token: string;
  role: "admin" | "operator";
  menu_ids: number[];
  created_by: number;
  created_by_display: string;
  created_at: string;
}

export interface ShareLinksPayload {
  links: ShareLink[];
  menus: { id: number; label: string; parent_id: number | null }[];
  public_pages: { id: number; title: string }[];
}

export interface ShareAcceptResult {
  deployment_id: number;
  entry_path: string;
  biz_name: string;
  role: string;
}

export const ShareApi = {
  /** 分享链接列表 + 可选菜单 + 公开页（创建人独有 DR-14）。 */
  list: (deploymentId: number) =>
    http
      .get<ShareLinksPayload>(`/deployments/${deploymentId}/share-links`)
      .then((r) => r.data),
  create: (deploymentId: number, payload: { role: "admin" | "operator"; menu_ids?: number[] }) =>
    http
      .post<ShareLink>(`/deployments/${deploymentId}/share-links`, payload)
      .then((r) => r.data),
  revoke: (deploymentId: number, linkId: number) =>
    http
      .delete<{ ok: boolean }>(`/deployments/${deploymentId}/share-links/${linkId}`)
      .then((r) => r.data),
  /** 进入分享链接即绑定为该部署成员（需登录）。 */
  accept: (token: string) =>
    http
      .post<ShareAcceptResult>(`/share/${encodeURIComponent(token)}/accept`)
      .then((r) => r.data),
};

// ---------- 认证 ----------
export interface CurrentUser {
  id: number;
  username: string;
  display_name: string;
  is_platform_admin: boolean;
}

export interface AuthApi {
  login: (payload: { username: string; password: string }) => Promise<CurrentUser>;
  logout: () => Promise<void>;
  me: () => Promise<CurrentUser>;
  setupStatus: () => Promise<{
    needs_setup: boolean;
    llm_configured: boolean;
    user_count: number;
  }>;
  bootstrap: () => Promise<{ created?: string[]; skipped?: boolean; [k: string]: unknown }>;
  listUsers: () => Promise<CurrentUser[]>;
  createUser: (payload: {
    username: string;
    password: string;
    display_name?: string;
    is_platform_admin?: boolean;
  }) => Promise<CurrentUser>;
  updateUser: (
    id: number,
    payload: { display_name?: string; password?: string; is_platform_admin?: boolean },
  ) => Promise<CurrentUser>;
}

export type ColumnOp =
  | { action: "add"; name: string; biz_name?: string; type: string; length?: number | null }
  | { action: "drop"; name: string }
  | { action: "rename"; name: string; new_name: string }
  | { action: "change_type"; name: string; type: string; length?: number | null };

// ---------- 工具 ----------
// 列表接口统一校验：后端未正确运行 / 被网关返回 HTML 或错误对象时，
// 抛出可读错误交给页面 catch 提示，避免渲染层 .find/.map 直接白屏。
function ensureArray<T>(data: unknown, what: string): T[] {
  if (!Array.isArray(data)) {
    throw new Error(`${what}返回数据格式异常（应为数组），请确认后端服务已正确启动`);
  }
  return data;
}

// ---------- 部署 ----------
export const DeployApi = {
  list: () =>
    http.get<Deployment[]>("/deployments").then((r) => ensureArray<Deployment>(r.data, "部署清单")),
  preview: (description: string) =>
    // LLM 生成结构实测 20s+，自检重试最多 3 次（每次服务端上限 60s），
    // 前端 30s 默认超时会在正常耗时内误报 timeout，此处放宽到 240s。
    http.post<Proposal>("/deployments/preview", { description }, { timeout: 240_000 })
      .then((r) => r.data),
  confirm: (payload: {
    spec_json: Record<string, unknown>;
    biz_name: string;
    description: string;
    seed_data: boolean;
    mode?: "create" | "link";
    connection_id?: number;
  }) => http.post<DeployResult>("/deployments/confirm", payload).then((r) => r.data),
  tables: (id: number) =>
    http.get<TableInfo[]>(`/deployments/${id}/tables`).then((r) => ensureArray<TableInfo>(r.data, "数据表")),
  rows: (
    id: number,
    table: string,
    params: { page?: number; page_size?: number; sort?: string; order?: string },
  ) =>
    http
      .get<RowPage>(`/deployments/${id}/tables/${encodeURIComponent(table)}/rows`, { params })
      .then((r) => r.data),
  blueprint: (id: number) =>
    http.get<TableBlueprint[]>(`/deployments/${id}/blueprint`).then((r) => ensureArray<TableBlueprint>(r.data, "字段蓝图")),
  /** M2-8：结构变更 diff 提案（只算不执行；确认仍走 alterColumns 落地）。 */
  proposeStructure: (id: number, table: string, ops: ColumnOp[]) =>
    http
      .post<StructureProposal>(`/deployments/${id}/structure/propose`, { table, ops })
      .then((r) => r.data),
  alterColumns: (id: number, table: string, ops: ColumnOp[]) =>
    http
      .post<{ name: string; columns: FieldColumn[]; statements?: string[] }>(
        `/deployments/${id}/tables/${encodeURIComponent(table)}/columns`,
        { ops },
      )
      .then((r) => r.data),
  /** M3-1：生命周期 停止/再启动（创建人独有） */
  stop: (id: number) =>
    http.post<DeployResult>(`/deployments/${id}/stop`).then((r) => r.data),
  start: (id: number) =>
    http.post<DeployResult>(`/deployments/${id}/start`).then((r) => r.data),
};

// ---------- 模型 ----------
export const ModelsApi = {
  list: () =>
    http.get<LlmModel[]>("/settings/models").then((r) => ensureArray<LlmModel>(r.data, "模型列表")),
  create: (data: Partial<LlmModel> & { api_key: string }) =>
    http.post<LlmModel>("/settings/models", data).then((r) => r.data),
  update: (id: number, data: Partial<LlmModel>) =>
    http.put<LlmModel>(`/settings/models/${id}`, data).then((r) => r.data),
  remove: (id: number) => http.delete(`/settings/models/${id}`),
  setDefault: (id: number) =>
    http.post<LlmModel>(`/settings/models/${id}/default`).then((r) => r.data),
  test: (id: number) =>
    http.post<{ ok: boolean; reply?: unknown; error?: string }>(
      `/settings/models/${id}/test`,
    ).then((r) => r.data),
};

// ---------- 直连模式（M2-7） ----------
export const LinkApi = {
  connections: () =>
    http
      .get<Connection[]>("/deployments/link/connections")
      .then((r) => ensureArray<Connection>(r.data, "可直连连接列表")),
  inventory: (connectionId: number) =>
    http
      .post<LinkTableInventory[]>("/deployments/link/inventory", { connection_id: connectionId })
      .then((r) => ensureArray<LinkTableInventory>(r.data, "表清单")),
  select: (connectionId: number, purpose: string) =>
    http
      .post<LinkSelection>("/deployments/link/select", {
        connection_id: connectionId,
        purpose,
      })
      .then((r) => r.data),
  preview: (payload: {
    connection_id: number;
    tables: string[];
    purpose?: string;
    enhance?: boolean;
  }) =>
    http
      .post<LinkProposal>("/deployments/link/preview", payload, { timeout: 120_000 })
      .then((r) => r.data),
};

// ---------- 连接 ----------
export const ConnectionsApi = {
  list: () =>
    http.get<Connection[]>("/settings/connections").then((r) => ensureArray<Connection>(r.data, "连接列表")),
  create: (data: Record<string, unknown>) =>
    http.post<Connection>("/settings/connections", data).then((r) => r.data),
  remove: (id: number) => http.delete(`/settings/connections/${id}`),
  test: (id: number) =>
    http.post<{ ok: boolean; status?: string; error?: string }>(
      `/settings/connections/${id}/test`,
    ).then((r) => r.data),
};

// ---------- Redis ----------
export const RedisApi = {
  get: () => http.get<RedisConfig>("/settings/redis").then((r) => r.data),
  save: (data: Partial<RedisConfig>) =>
    http.put<RedisConfig>("/settings/redis", data).then((r) => r.data),
  test: () =>
    http.post<{ ok: boolean; version?: string; error?: string }>(
      "/settings/redis/test",
    ).then((r) => r.data),
};

// ---------- 认证 ----------
export const AuthApi: AuthApi = {
  login: (payload) =>
    http.post<CurrentUser>("/auth/login", payload).then((r) => r.data),
  logout: () =>
    http.post("/auth/logout").then(() => {
      /* noop */
    }),
  me: () => http.get<CurrentUser>("/auth/me").then((r) => r.data),
  setupStatus: () =>
    http.get<{ needs_setup: boolean; llm_configured: boolean; user_count: number }>(
      "/auth/setup-status",
    ).then((r) => r.data),
  bootstrap: () =>
    http.post<{ created?: string[]; skipped?: boolean; [k: string]: unknown }>(
      "/auth/bootstrap",
    ).then((r) => r.data),
  listUsers: () =>
    http.get<CurrentUser[]>("/auth/users").then((r) => ensureArray<CurrentUser>(r.data, "用户列表")),
  createUser: (payload) =>
    http.post<CurrentUser>("/auth/users", payload).then((r) => r.data),
  updateUser: (id, payload) =>
    http.put<CurrentUser>(`/auth/users/${id}`, payload).then((r) => r.data),
};
