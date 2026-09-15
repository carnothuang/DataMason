import axios from "axios";

// 开发环境经 vite proxy 转发到 Flask；生产同源
export const http = axios.create({ baseURL: "/api/v1", timeout: 30000 });

http.interceptors.response.use(
  (resp) => resp,
  (error) => {
    const msg =
      error?.response?.data?.error ||
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
}

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

// ---------- 部署 ----------
export const DeployApi = {
  list: () => http.get<Deployment[]>("/deployments").then((r) => r.data),
  preview: (description: string) =>
    http.post<Proposal>("/deployments/preview", { description }).then((r) => r.data),
  confirm: (payload: {
    spec_json: Record<string, unknown>;
    biz_name: string;
    description: string;
    seed_data: boolean;
  }) => http.post<DeployResult>("/deployments/confirm", payload).then((r) => r.data),
  tables: (id: number) =>
    http.get<TableInfo[]>(`/deployments/${id}/tables`).then((r) => r.data),
  rows: (
    id: number,
    table: string,
    params: { page?: number; page_size?: number; sort?: string; order?: string },
  ) =>
    http
      .get<RowPage>(`/deployments/${id}/tables/${encodeURIComponent(table)}/rows`, { params })
      .then((r) => r.data),
};

// ---------- 模型 ----------
export const ModelsApi = {
  list: () => http.get<LlmModel[]>("/settings/models").then((r) => r.data),
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

// ---------- 连接 ----------
export const ConnectionsApi = {
  list: () => http.get<Connection[]>("/settings/connections").then((r) => r.data),
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
