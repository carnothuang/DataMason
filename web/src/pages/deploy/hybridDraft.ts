/** M6-3 混合建库草稿状态（DR-37/38）：单一数据源 = 字段定义。
 *
 * 画布与向导都只读写这一份草稿（useReducer）；关系（连线）由字段中的外键列
 * 派生，不单独存一份关系真相。草稿全程在浏览器内存，最后合并为完整 spec 一次提交。
 */
export type HybridColumnType =
  | "INTEGER"
  | "BIGINT"
  | "VARCHAR"
  | "TEXT"
  | "BOOLEAN"
  | "DATETIME"
  | "DECIMAL"
  | "FILE";

export interface HybridColumn {
  key: string; // 仅前端稳定 key，提交前剥离
  name: string;
  biz_name: string;
  type: HybridColumnType;
  length: number | null;
  nullable: boolean;
  unique: boolean;
  is_pk: boolean;
  is_fk: boolean;
  ref_table: string | null;
  ref_column: string | null;
  is_ownership: boolean;
}

export interface HybridTable {
  name: string;
  biz_name: string;
  biz_notes: string;
  columns: HybridColumn[];
}

export interface HybridDraft {
  biz_notes: string;
  tables: HybridTable[];
}

export interface SkeletonTableJson {
  name: string;
  biz_name?: string;
  biz_notes?: string;
  relations?: { to_table: string; note?: string }[];
}

export type ColumnInput = Partial<HybridColumn> & { name: string };

let seq = 0;
const nextKey = () => `c-${Date.now().toString(36)}-${seq++}`;

const OWNERSHIP = new Set(["created_by", "created_at", "updated_by", "updated_at"]);
export const isOwnershipName = (name: string) => OWNERSHIP.has(name);

export function normalizeColumn(raw: ColumnInput): HybridColumn {
  const is_ownership = !!(raw.is_ownership || OWNERSHIP.has(raw.name));
  const is_fk = !!(raw.is_fk && raw.ref_table);
  return {
    key: raw.key ?? nextKey(),
    name: raw.name,
    biz_name: raw.biz_name ?? "",
    type: (raw.type as HybridColumnType) ?? "VARCHAR",
    length: raw.length ?? (raw.type === "VARCHAR" ? 255 : null),
    nullable: raw.nullable ?? !raw.is_pk,
    unique: raw.unique ?? false,
    is_pk: raw.is_pk ?? false,
    is_fk,
    ref_table: is_fk ? raw.ref_table ?? null : null,
    ref_column: is_fk ? raw.ref_column ?? "id" : null,
    is_ownership,
  };
}

/** 后端骨架 + 外键占位列映射 → 前端草稿（Step 2 落草稿）。 */
export function draftFromSkeleton(
  skeleton: { biz_notes?: string; tables: SkeletonTableJson[] },
  columns: Record<string, ColumnInput[]>,
): HybridDraft {
  return {
    biz_notes: skeleton.biz_notes ?? "",
    tables: (skeleton.tables || []).map((t) => ({
      name: t.name,
      biz_name: t.biz_name ?? "",
      biz_notes: t.biz_notes ?? "",
      columns: (columns[t.name] || []).map(normalizeColumn),
    })),
  };
}

export interface HybridEdge {
  from: string;
  to: string;
  column: string;
  label: string;
}

/** 关系连线的唯一真相：从各表外键列派生（DR-38）。 */
export function edgesOf(draft: HybridDraft): HybridEdge[] {
  const names = new Set(draft.tables.map((t) => t.name));
  const edges: HybridEdge[] = [];
  for (const t of draft.tables) {
    for (const c of t.columns) {
      if (c.is_fk && c.ref_table && names.has(c.ref_table) && c.ref_table !== t.name) {
        edges.push({ from: t.name, to: c.ref_table, column: c.name, label: "属于" });
      }
    }
  }
  return edges;
}

/** 未引导字段（无任何列 / 仅系统主键）的表名，供 Step 4 落库拦截。 */
export function unguidedTables(draft: HybridDraft): string[] {
  return draft.tables
    .filter((t) => t.columns.filter((c) => !c.is_pk && !c.is_ownership).length === 0)
    .map((t) => t.name);
}

/** 提交给后端合并器的 columns_map：剥离前端 key。 */
export function toColumnsMap(draft: HybridDraft): Record<string, Omit<HybridColumn, "key">[]> {
  const out: Record<string, Omit<HybridColumn, "key">[]> = {};
  for (const t of draft.tables) {
    out[t.name] = t.columns.map((c) => ({
      name: c.name,
      biz_name: c.biz_name,
      type: c.type,
      length: c.length,
      nullable: c.nullable,
      unique: c.unique,
      is_pk: c.is_pk,
      is_fk: c.is_fk,
      ref_table: c.ref_table,
      ref_column: c.ref_column,
      is_ownership: c.is_ownership,
    }));
  }
  return out;
}

/** 由草稿反推骨架（关系从外键列派生），供 M6-5 合并接口。 */
export function toSkeletonJson(draft: HybridDraft): {
  biz_notes: string;
  tables: SkeletonTableJson[];
} {
  return {
    biz_notes: draft.biz_notes,
    tables: draft.tables.map((t) => ({
      name: t.name,
      biz_name: t.biz_name,
      biz_notes: t.biz_notes,
      relations: t.columns
        .filter((c) => c.is_fk && c.ref_table)
        .map((c) => ({ to_table: c.ref_table as string })),
    })),
  };
}

function uniqueName(base: string, taken: Set<string>): string {
  if (!taken.has(base)) return base;
  let n = 2;
  while (taken.has(`${base}_${n}`)) n += 1;
  return `${base}_${n}`;
}

export type HybridAction =
  | { type: "reset"; draft: HybridDraft }
  | { type: "setBizNotes"; value: string }
  | { type: "addTable"; name: string; biz_name?: string }
  | { type: "removeTable"; name: string }
  | { type: "updateTable"; name: string; patch: Partial<Pick<HybridTable, "name" | "biz_name" | "biz_notes">> }
  | { type: "setColumns"; table: string; columns: ColumnInput[] }
  | { type: "addColumn"; table: string; column: ColumnInput }
  | { type: "updateColumn"; table: string; key: string; patch: Partial<HybridColumn> }
  | { type: "removeColumn"; table: string; key: string }
  | { type: "addRelation"; from: string; to: string; columnName?: string }
  | { type: "removeRelation"; from: string; column: string };

function mapTable(
  draft: HybridDraft,
  name: string,
  fn: (t: HybridTable) => HybridTable,
): HybridDraft {
  return { ...draft, tables: draft.tables.map((t) => (t.name === name ? fn(t) : t)) };
}

export function hybridReducer(draft: HybridDraft, action: HybridAction): HybridDraft {
  switch (action.type) {
    case "reset":
      return action.draft;
    case "setBizNotes":
      return { ...draft, biz_notes: action.value };
    case "addTable": {
      const name = action.name.trim();
      if (!name || draft.tables.some((t) => t.name === name)) return draft;
      const table: HybridTable = {
        name,
        biz_name: action.biz_name?.trim() || name,
        biz_notes: "",
        columns: [
          normalizeColumn({
            name: "id", biz_name: "编号", type: "INTEGER",
            is_pk: true, nullable: false,
          }),
        ],
      };
      return { ...draft, tables: [...draft.tables, table] };
    }
    case "removeTable": {
      // 删表同时清理指向它的外键列，避免出现悬空关系（单一数据源）
      const tables = draft.tables
        .filter((t) => t.name !== action.name)
        .map((t) => ({
          ...t,
          columns: t.columns.map((c) =>
            c.is_fk && c.ref_table === action.name
              ? normalizeColumn({ ...c, is_fk: false, ref_table: null, ref_column: null })
              : c,
          ),
        }));
      return { ...draft, tables };
    }
    case "updateTable": {
      const oldName = action.name;
      const newName = action.patch.name?.trim();
      if (newName && newName !== oldName && draft.tables.some((t) => t.name === newName)) {
        return draft;
      }
      const tables = draft.tables.map((t) => {
        const patched: HybridTable =
          t.name === oldName ? { ...t, ...action.patch, name: newName || t.name } : t;
        // 表改名 → 其它表指向它的外键列同步改 ref_table（关系联动）
        if (newName && newName !== oldName) {
          patched.columns = patched.columns.map((c) =>
            c.is_fk && c.ref_table === oldName ? { ...c, ref_table: newName } : c,
          );
        }
        return patched;
      });
      return { ...draft, tables };
    }
    case "setColumns":
      return mapTable(draft, action.table, (t) => ({
        ...t,
        columns: action.columns.map(normalizeColumn),
      }));
    case "addColumn":
      return mapTable(draft, action.table, (t) => {
        const taken = new Set(t.columns.map((c) => c.name));
        const col = normalizeColumn(action.column);
        col.name = uniqueName(col.name, taken);
        return { ...t, columns: [...t.columns, col] };
      });
    case "updateColumn":
      return mapTable(draft, action.table, (t) => ({
        ...t,
        columns: t.columns.map((c) => {
          if (c.key !== action.key) return c;
          const merged = { ...c, ...action.patch };
          if (!merged.is_fk) {
            merged.ref_table = null;
            merged.ref_column = null;
          } else if (!merged.ref_column) {
            merged.ref_column = "id";
          }
          return merged;
        }),
      }));
    case "removeColumn":
      return mapTable(draft, action.table, (t) => ({
        ...t,
        columns: t.columns.filter((c) => c.key !== action.key),
      }));
    case "addRelation": {
      if (action.from === action.to) return draft;
      if (!draft.tables.some((t) => t.name === action.from)) return draft;
      const target = draft.tables.find((t) => t.name === action.to);
      if (!target) return draft;
      return mapTable(draft, action.from, (t) => {
        const taken = new Set(t.columns.map((c) => c.name));
        const base = (action.columnName?.trim() || `${action.to}_id`);
        const name = uniqueName(base, taken);
        return {
          ...t,
          columns: [
            ...t.columns,
            normalizeColumn({
              name,
              biz_name: `${target.biz_name || action.to}编号`,
              type: "INTEGER",
              nullable: true,
              is_fk: true,
              ref_table: action.to,
              ref_column: "id",
            } as ColumnInput),
          ],
        };
      });
    }
    case "removeRelation":
      return mapTable(draft, action.from, (t) => ({
        ...t,
        columns: t.columns.filter((c) => c.name !== action.column),
      }));
    default:
      return draft;
  }
}
