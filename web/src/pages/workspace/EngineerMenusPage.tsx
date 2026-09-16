import { useCallback, useEffect, useRef, useState } from "react";
import {
  Alert,
  App,
  Button,
  Empty,
  Input,
  Modal,
  Popconfirm,
  Space,
  Spin,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import {
  AppstoreOutlined,
  CheckOutlined,
  CloseOutlined,
  DeleteOutlined,
  EditOutlined,
  FolderOutlined,
  HolderOutlined,
  LinkOutlined,
  PlusOutlined,
} from "@ant-design/icons";
import {
  CrudApi,
  EngineerApi,
  EngineerMenuItem,
  EngineerPageCard,
  EngineerInterfacesPayload,
} from "../../api";

const { Text } = Typography;

const ENTRY_RE = /^\/[A-Za-z0-9][A-Za-z0-9_\-]*(?:\/[A-Za-z0-9_\-]+)*$/;
const ENTRY_RESERVED = ["/app", "/login", "/deployments", "/settings", "/assets", "/api"];
const MAX_ENTRY_LEN = 64;
const KIND_LABEL: Record<string, string> = {
  crud_list: "数据清单",
  public_form: "公开表单",
  dashboard: "看板",
  flow: "流程向导",
};

interface TreeNode {
  key: string;
  id: number | null;
  label: string;
  kind: string;
  ref_id: number | null;
  children: TreeNode[];
}

interface PagePreview {
  rows?: Record<string, unknown>[];
  columns?: string[];
  formColumns?: { name: string; biz_name?: string }[];
}

interface FlatNode {
  id: number | null;
  label: string;
  kind: string;
  ref_id: number | null;
  parent_id: number | null;
}

type DropPos = "before" | "inside" | "after";

interface DropMark {
  key: string;
  pos: DropPos;
}

function buildTree(menus: EngineerMenuItem[]): TreeNode[] {
  const byId = new Map<number, TreeNode>();
  for (const m of menus) {
    byId.set(m.id, {
      key: `m:${m.id}`,
      id: m.id,
      label: m.label,
      kind: m.kind,
      ref_id: m.ref_id,
      children: [],
    });
  }
  const roots: TreeNode[] = [];
  for (const m of menus) {
    const node = byId.get(m.id)!;
    if (m.parent_id != null && byId.has(m.parent_id)) {
      byId.get(m.parent_id)!.children.push(node);
    } else {
      roots.push(node);
    }
  }
  return roots;
}

function cloneTree(nodes: TreeNode[]): TreeNode[] {
  return nodes.map((n) => ({ ...n, children: cloneTree(n.children) }));
}

function findSpot(nodes: TreeNode[], key: string): { list: TreeNode[]; index: number } | null {
  for (let i = 0; i < nodes.length; i++) {
    if (nodes[i].key === key) return { list: nodes, index: i };
    const inner = findSpot(nodes[i].children, key);
    if (inner) return inner;
  }
  return null;
}

function extractNode(nodes: TreeNode[], key: string): TreeNode | null {
  for (let i = 0; i < nodes.length; i++) {
    if (nodes[i].key === key) return nodes.splice(i, 1)[0];
  }
  for (const n of nodes) {
    const r = extractNode(n.children, key);
    if (r) return r;
  }
  return null;
}

function insertNodeAt(nodes: TreeNode[], node: TreeNode, anchorKey: string, pos: DropPos): void {
  const spot = findSpot(nodes, anchorKey);
  if (!spot) return;
  if (pos === "inside") {
    spot.list[spot.index].children.push(node);
  } else {
    spot.list.splice(spot.index + (pos === "after" ? 1 : 0), 0, node);
  }
}

/** 把 node 插入到 anchor 参照位置（用于卡片拖拽新增菜单节点）。 */
function insertNewNode(nodes: TreeNode[], node: TreeNode, anchorKey: string, pos: DropPos): TreeNode[] {
  const next = cloneTree(nodes);
  insertNodeAt(next, node, anchorKey, pos);
  return next;
}

/** 树内拖动：先摘除源节点，再插入目标位置（摘除后的树重查 anchor）。 */
function moveNode(nodes: TreeNode[], dragKey: string, anchorKey: string, pos: DropPos): TreeNode[] | null {
  const next = cloneTree(nodes);
  const removed = extractNode(next, dragKey);
  if (!removed) return null;
  insertNodeAt(next, removed, anchorKey, pos);
  return next;
}

/** 前序拍平；parent_id：有实 id 用正数，新建节点用负数（-1 = 列表第 1 个节点）供后端解析。 */
function flattenTree(nodes: TreeNode[], granted: Set<number>): FlatNode[] {
  const out: FlatNode[] = [];
  const posByKey = new Map<string, number>();
  const walk = (list: TreeNode[], parentKey: string | null) => {
    for (const n of list) {
      out.push({
        id: n.id,
        label: n.label,
        kind: n.kind,
        ref_id: n.ref_id,
        parent_id: null,
        parentRef: parentKey as string | null,
      } as FlatNode & { parentRef: string | null });
      posByKey.set(n.key, out.length);
      walk(n.children, n.key);
    }
  };
  walk(nodes, null);
  for (const f of out as (FlatNode & { parentRef: string | null })[]) {
    const parentKey = f.parentRef;
    if (!parentKey) continue;
    if (parentKey.startsWith("m:") && granted.has(Number(parentKey.slice(2)))) {
      f.parent_id = Number(parentKey.slice(2));
    } else {
      const parentPos = posByKey.get(parentKey);
      if (parentPos != null) f.parent_id = -parentPos;
    }
  }
  for (const f of out as (FlatNode & { parentRef: string | null })[]) delete (f as { parentRef?: string | null }).parentRef;
  return out;
}

interface Props {
  deploymentId: number;
}

/** M3-2 工程室「界面与菜单」：
 *  真实界面缩略卡（可运行、带演示数据）+ 拖拽进左侧导航树即拖即存 + 自定义入口路径（DR-15）+
 *  新菜单默认不授权（M2-5 体系，明示「需授权给操作员」）。创建人独有（DR-14）。 */
export default function EngineerMenusPage({ deploymentId }: Props) {
  const { message } = App.useApp();
  const [data, setData] = useState<EngineerInterfacesPayload | null>(null);
  const [tree, setTree] = useState<TreeNode[]>([]);
  const [previews, setPreviews] = useState<Record<number, PagePreview>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [entryPath, setEntryPath] = useState("");
  const [adjustPage, setAdjustPage] = useState<EngineerPageCard | null>(null);
  const [adjustTitle, setAdjustTitle] = useState("");
  const [renaming, setRenaming] = useState<{ key: string; value: string } | null>(null);
  const [dropMark, setDropMark] = useState<DropMark | null>(null);
  const dragKey = useRef<string | null>(null);

  const refresh = useCallback(async () => {
    const d = await EngineerApi.interfaces(deploymentId);
    setData(d);
    setTree(buildTree(d.menus));
    setEntryPath(d.entry_path);
    return d;
  }, [deploymentId]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      await refresh();
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [refresh, message]);

  useEffect(() => {
    load();
  }, [load]);

  const fetchPreviews = useCallback(
    async (pages: EngineerPageCard[]) => {
      const results = await Promise.all(
        pages.map(async (p) => {
          const out: PagePreview = {};
          if (!p.table) return [p.id, out] as const;
          try {
            if (p.kind === "crud_list") {
              const page = await CrudApi.rows(deploymentId, p.table, { page: 1, page_size: 3 });
              out.rows = page.data;
              out.columns = page.columns;
            } else if (p.kind === "public_form") {
              const meta = await CrudApi.meta(deploymentId);
              const t = meta[p.table];
              if (t) {
                out.formColumns = t.columns
                  .filter((c) => c.name !== "id")
                  .slice(0, 4)
                  .map((c) => ({ name: c.name, biz_name: c.biz_name }));
              }
            }
          } catch {
            /* 缩略图不阻塞拖拽 */
          }
          return [p.id, out] as const;
        }),
      );
      setPreviews(Object.fromEntries(results));
    },
    [deploymentId],
  );

  useEffect(() => {
    if (data) fetchPreviews(data.pages);
  }, [data, fetchPreviews]);

  const saveTree = async (next: TreeNode[], okMsg: string) => {
    const granted = new Set(data?.granted_menu_ids ?? []);
    setSaving(true);
    try {
      await EngineerApi.saveMenus(deploymentId, flattenTree(next, granted));
      await refresh();
      message.success(okMsg);
    } catch (e) {
      message.error((e as Error).message);
      try {
        await refresh();
      } catch {
        /* ignore */
      }
    } finally {
      setSaving(false);
    }
  };

  const computeDropPos = (e: React.DragEvent): DropPos => {
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    const y = e.clientY - rect.top;
    if (y < rect.height * 0.3) return "before";
    if (y > rect.height * 0.7) return "after";
    return "inside";
  };

  const handleCardDragStart = (e: React.DragEvent, pageId: number) => {
    dragKey.current = `card:${pageId}`;
    e.dataTransfer.effectAllowed = "copy";
    e.dataTransfer.setData("text/plain", `card:${pageId}`);
  };

  const onNodeDragOver = (key: string) => (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    e.dataTransfer.dropEffect = dragKey.current?.startsWith("card:") ? "copy" : "move";
    setDropMark({ key, pos: computeDropPos(e) });
  };

  const onNodeDrop = (anchorKey: string) => (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDropMark(null);
    const src = dragKey.current;
    if (!src) return;
    const pos = computeDropPos(e);
    const anchor = findSpot(tree, anchorKey);
    if (!anchor) return;

    if (src.startsWith("card:")) {
      const pageId = Number(src.slice("card:".length));
      const page = data?.pages.find((p) => p.id === pageId);
      if (!page) return;
      const node: TreeNode = {
        key: `new:${Date.now()}`,
        id: null,
        label: page.title && page.title !== page.table_biz_name ? page.title : page.table_biz_name || page.title || "新菜单",
        kind: page.kind === "flow" ? "flow" : page.kind === "dashboard" ? "dashboard" : "table",
        ref_id: page.id,
        children: [],
      };
      void saveTree(insertNewNode(tree, node, anchorKey, pos), `已添加「${node.label}」`);
      return;
    }

    const next = moveNode(tree, src, anchorKey, pos === "inside" && src === anchorKey ? "after" : pos);
    if (!next) return;
    void saveTree(next, "菜单已更新");
  };

  const onNodeDragLeave = (key: string) => (e: React.DragEvent) => {
    if (e.currentTarget.contains(e.relatedTarget as Node)) return;
    setDropMark((m) => (m?.key === key ? null : m));
  };

  const addGroup = async (parentKey: string | null) => {
    const baseLabel = "新分组";
    let label = baseLabel;
    let i = 2;
    while (tree.some((n) => n.label === label)) label = `${baseLabel}${i++}`;
    const node: TreeNode = { key: `new:${Date.now()}`, id: null, label, kind: "table", ref_id: null, children: [] };
    const target = parentKey ? findSpot(tree, parentKey) : null;
    let next: TreeNode[];
    if (target) {
      next = cloneTree(tree);
      target.list[target.index].children.push(node);
    } else {
      next = [...cloneTree(tree), node];
    }
    await saveTree(next, `已新建分组「${label}」`);
  };

  const renameCommit = async () => {
    if (!renaming) return;
    const label = renaming.value.trim();
    if (!label) {
      setRenaming(null);
      return;
    }
    const next = cloneTree(tree);
    const spot = findSpot(next, renaming.key);
    setRenaming(null);
    if (!spot) return;
    spot.list[spot.index].label = label;
    await saveTree(next, "已重命名");
  };

  const deleteNode = async (key: string, label: string) => {
    const next = cloneTree(tree);
    const removed = extractNode(next, key);
    if (!removed) return;
    await saveTree(next, `已删除「${label}」`);
  };

  const saveEntryPath = async () => {
    const p = entryPath.trim();
    // 留空 = 恢复默认入口 /app/{id}（DR-15）
    if (p) {
      if (!p.startsWith("/")) {
        message.error("入口路径必须以 / 开头，如 /guestbook");
        return;
      }
      if (p.length > MAX_ENTRY_LEN) {
        message.error(`入口路径不能超过 ${MAX_ENTRY_LEN} 字符`);
        return;
      }
      if (!ENTRY_RE.test(p)) {
        message.error("路径仅允许字母、数字、下划线、短横线与斜杠（每段以字母/数字开头）");
        return;
      }
      if (ENTRY_RESERVED.some((r) => p === r || p.startsWith(`${r}/`)) || p.startsWith("/api/") || p === "/api") {
        message.error("该路径为系统保留，请换一个（如 /guestbook）");
        return;
      }
    }
    setSaving(true);
    try {
      const res = await EngineerApi.setEntryPath(deploymentId, p);
      setEntryPath(res.entry_path);
      await refresh();
      message.success(`入口路径已更新为 ${res.entry_path}`);
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const saveAdjustTitle = async () => {
    if (!adjustPage) return;
    const title = adjustTitle.trim();
    if (!title) {
      message.error("页面标题不能为空");
      return;
    }
    try {
      await EngineerApi.updatePageTitle(deploymentId, adjustPage.id, title);
      setAdjustPage(null);
      await refresh();
      message.success("已更新页面标题");
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  if (loading && !data) {
    return (
      <div style={{ display: "flex", justifyContent: "center", padding: 48 }}>
        <Spin tip="加载界面与菜单…" />
      </div>
    );
  }
  if (!data) {
    return <Empty description="界面与菜单加载失败" />;
  }

  const granted = new Set(data.granted_menu_ids);
  const grantedCount = data.menus.filter((m) => granted.has(m.id)).length;

  return (
    <div>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="拖拽左侧「界面卡」到下方导航树，即可把界面挂到工作台菜单（即拖即存）。新建菜单默认不会授权给操作员，需在「成员与权限」中勾选后他们才可见。"
      />
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        {/* 入口路径 */}
        <Space wrap style={{ alignItems: "center" }}>
          <LinkOutlined style={{ color: "#8c8c8c" }} />
          <Text strong>入口路径</Text>
          <Input
            style={{ width: 260 }}
            placeholder="/guestbook"
            value={entryPath}
            onChange={(e) => setEntryPath(e.target.value)}
            onPressEnter={saveEntryPath}
            suffix={
              entryPath === data.entry_path ? (
                <CheckOutlined style={{ color: "#52c41a" }} />
              ) : undefined
            }
          />
          {entryPath !== data.entry_path && (
            <Button type="primary" size="small" onClick={saveEntryPath}>
              保存路径
            </Button>
          )}
          <Tooltip title="自定义后，此路径直接作为浏览器访问入口（如 /guestbook）；旧路径 /app/{id} 不再暴露。留空可恢复默认。">
            <Text type="secondary" style={{ fontSize: 12 }}>
              当前：{data.entry_path}
            </Text>
          </Tooltip>
        </Space>

        <div style={{ display: "flex", gap: 24, alignItems: "flex-start" }}>
          {/* 左：菜单树 */}
          <div
            style={{
              flex: "0 0 300px",
              border: "1px solid #f0f0f0",
              borderRadius: 8,
              padding: 12,
              background: "#fff",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
              <Text strong>工作台导航树</Text>
              <Button
                size="small"
                type="dashed"
                icon={<PlusOutlined />}
                loading={saving}
                onClick={() => void addGroup(null)}
              >
                新建分组
              </Button>
            </div>
            {tree.length === 0 ? (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description="暂无菜单，拖拽右侧界面卡进来开始搭建"
              />
            ) : (
              <div onDragLeave={() => setDropMark(null)}>
                {tree.map((n) => (
                  <TreeNodeRow
                    key={n.key}
                    node={n}
                    depth={0}
                    granted={granted}
                    renaming={renaming}
                    dropMark={dropMark}
                    saving={saving}
                    onDragStart={onDragStart}
                    onDragOver={onNodeDragOver(n.key)}
                    onDragLeave={onNodeDragLeave(n.key)}
                    onDrop={onNodeDrop(n.key)}
                    onAddGroup={addGroup}
                    onStartRename={(key, value) => setRenaming({ key, value })}
                    onRenameChange={(value) => setRenaming((r) => (r ? { ...r, value } : r))}
                    onRenameCancel={() => setRenaming(null)}
                    onRenameCommit={renameCommit}
                    onDelete={deleteNode}
                  />
                ))}
              </div>
            )}
            <div style={{ marginTop: 12, fontSize: 12, color: "#8c8c8c" }}>
              共 {data.menus.length} 个菜单，已授权给操作员 {grantedCount} 个 · 未授权 <Tag color="warning">需授权</Tag>
            </div>
          </div>

          {/* 右：界面卡 */}
          <div style={{ flex: 1, minWidth: 0 }}>
            <Text strong style={{ display: "block", marginBottom: 8 }}>
              界面卡（拖入左侧导航树即存）
            </Text>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))", gap: 12 }}>
              {data.pages.map((p) => (
                <PageCard
                  key={p.id}
                  page={p}
                  preview={previews[p.id]}
                  onDragStart={(e) => handleCardDragStart(e, p.id)}
                  onAdjust={() => {
                    setAdjustPage(p);
                    setAdjustTitle(p.title);
                  }}
                />
              ))}
              {data.pages.length === 0 && (
                <div style={{ gridColumn: "1 / -1" }}>
                  <Empty description="暂无界面卡" />
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      <Modal
        title={`调整「${adjustPage?.title ?? ""}」`}
        open={adjustPage !== null}
        onOk={saveAdjustTitle}
        onCancel={() => setAdjustPage(null)}
        okText="保存"
        cancelText="取消"
        destroyOnClose
      >
        <div style={{ marginBottom: 8 }}>
          <Text type="secondary">界面卡「调整」当前支持改页面标题；完整页面编辑器在后续里程碑。</Text>
        </div>
        <Input
          value={adjustTitle}
          onChange={(e) => setAdjustTitle(e.target.value)}
          placeholder="页面标题"
          onPressEnter={saveAdjustTitle}
          maxLength={128}
        />
      </Modal>
    </div>
  );

  function onDragStart(e: React.DragEvent, key: string) {
    dragKey.current = key;
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", key);
  }
}

function TreeNodeRow({
  node,
  depth,
  granted,
  renaming,
  dropMark,
  saving,
  onDragStart,
  onDragOver,
  onDragLeave,
  onDrop,
  onAddGroup,
  onStartRename,
  onRenameChange,
  onRenameCancel,
  onRenameCommit,
  onDelete,
}: {
  node: TreeNode;
  depth: number;
  granted: Set<number>;
  renaming: { key: string; value: string } | null;
  dropMark: DropMark | null;
  saving: boolean;
  onDragStart: (e: React.DragEvent, key: string) => void;
  onDragOver: (e: React.DragEvent) => void;
  onDragLeave: (e: React.DragEvent) => void;
  onDrop: (e: React.DragEvent) => void;
  onAddGroup: (parentKey: string | null) => Promise<void>;
  onStartRename: (key: string, value: string) => void;
  onRenameChange: (value: string) => void;
  onRenameCancel: () => void;
  onRenameCommit: () => void;
  onDelete: (key: string, label: string) => Promise<void>;
}) {
  const isGroup = node.ref_id == null;
  const mark = dropMark?.key === node.key ? dropMark.pos : null;
  const isRenaming = renaming?.key === node.key;

  const rowStyle: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 4,
    padding: "4px 6px",
    borderRadius: 6,
    cursor: "grab",
    background: mark === "inside" ? "#fff7e6" : "transparent",
    border: mark === "inside" ? "1px dashed #fa8c16" : "1px solid transparent",
    userSelect: "none",
  };

  const indent = { paddingLeft: depth * 16 };

  return (
    <div>
      {mark === "before" && <div style={{ height: 2, margin: "2px 6px", background: "#1677ff", borderRadius: 1 }} />}
      <div style={{ ...indent }}>
        <div
          draggable={!saving}
          style={rowStyle}
          onDragStart={(e) => onDragStart(e, node.key)}
          onDragOver={onDragOver}
          onDragLeave={onDragLeave}
          onDrop={onDrop}
          onDoubleClick={() => onStartRename(node.key, node.label)}
        >
          <HolderOutlined style={{ color: "#bfbfbf", fontSize: 12 }} />
          {isGroup ? (
            <FolderOutlined style={{ color: "#faad14" }} />
          ) : (
            <AppstoreOutlined style={{ color: "#1677ff" }} />
          )}
          {isRenaming ? (
            <Input
              size="small"
              style={{ flex: 1, minWidth: 80 }}
              autoFocus
              value={renaming?.value ?? ""}
              onChange={(e) => onRenameChange(e.target.value)}
              onPressEnter={onRenameCommit}
              onBlur={onRenameCommit}
            />
          ) : (
            <Text
              ellipsis
              style={{ flex: 1, minWidth: 0 }}
              onClick={() => onStartRename(node.key, node.label)}
              title="双击重命名"
            >
              {node.label}
            </Text>
          )}
          {node.id != null && !granted.has(node.id) && (
            <Tag color="warning" style={{ marginRight: 0, fontSize: 11 }}>
              需授权
            </Tag>
          )}
          <Tooltip title="新建子分组">
            <Button
              type="text"
              size="small"
              icon={<PlusOutlined />}
              disabled={saving}
              onClick={(e) => {
                e.stopPropagation();
                void onAddGroup(node.key);
              }}
            />
          </Tooltip>
          <Popconfirm
            title={`删除「${node.label}」及其子菜单？`}
            description="删除后该菜单及其授权记录一并移除。"
            onConfirm={() => void onDelete(node.key, node.label)}
          >
            <Button type="text" size="small" danger icon={<DeleteOutlined />} disabled={saving} />
          </Popconfirm>
        </div>
      </div>
      {node.children.length > 0 && (
        <div>
          {node.children.map((c) => (
            <TreeNodeRow
              key={c.key}
              node={c}
              depth={depth + 1}
              granted={granted}
              renaming={renaming}
              dropMark={dropMark}
              saving={saving}
              onDragStart={onDragStart}
              onDragOver={onDragOver}
              onDragLeave={onDragLeave}
              onDrop={onDrop}
              onAddGroup={onAddGroup}
              onStartRename={onStartRename}
              onRenameChange={onRenameChange}
              onRenameCancel={onRenameCancel}
              onRenameCommit={onRenameCommit}
              onDelete={onDelete}
            />
          ))}
        </div>
      )}
      {mark === "after" && <div style={{ height: 2, margin: "2px 6px", background: "#1677ff", borderRadius: 1 }} />}
    </div>
  );
}

function PageCard({
  page,
  preview,
  onDragStart,
  onAdjust,
}: {
  page: EngineerPageCard;
  preview: PagePreview | undefined;
  onDragStart: (e: React.DragEvent) => void;
  onAdjust: () => void;
}) {
  const [dragging, setDragging] = useState(false);
  return (
    <div
      draggable
      onDragStart={(e) => {
        setDragging(true);
        onDragStart(e);
      }}
      onDragEnd={() => setDragging(false)}
      style={{
        border: dragging ? "1px dashed #1677ff" : "1px solid #f0f0f0",
        borderRadius: 8,
        padding: 10,
        background: "#fff",
        cursor: "grab",
        boxShadow: dragging ? "0 2px 8px rgba(22,119,255,0.24)" : "0 1px 2px rgba(0,0,0,0.04)",
        transition: "box-shadow .2s",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 8 }}>
        <HolderOutlined style={{ color: "#bfbfbf", fontSize: 12 }} />
        <Text strong ellipsis style={{ flex: 1, minWidth: 0 }}>
          {page.title}
        </Text>
        <Tag
          color={
            page.kind === "crud_list"
              ? "blue"
              : page.kind === "public_form"
                ? "green"
                : page.kind === "flow"
                  ? "magenta"
                  : "purple"
          }
        >
          {KIND_LABEL[page.kind] ?? page.kind}
        </Tag>
      </div>
      {page.table && (
        <Text type="secondary" style={{ fontSize: 12 }}>
          数据表：{page.table_biz_name || page.table}
        </Text>
      )}
      <div style={{ marginTop: 8, fontSize: 12 }}>
        {page.kind === "crud_list" && preview?.rows && preview.columns ? (
          <Table
            size="small"
            rowKey={(_, i) => String(i)}
            pagination={false}
            scroll={{ x: true }}
            columns={preview.columns.slice(0, 4).map((c) => ({ title: c, dataIndex: c, ellipsis: true }))}
            dataSource={preview.rows}
            locale={{ emptyText: "暂无数据" }}
          />
        ) : page.kind === "public_form" && preview?.formColumns ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 6, padding: "4px 2px" }}>
            {preview.formColumns.map((c) => (
              <div key={c.name} style={{ display: "flex", justifyContent: "space-between", gap: 6 }}>
                <span style={{ color: "#8c8c8c" }}>{c.biz_name || c.name}</span>
                <span
                  style={{
                    flex: 1,
                    borderBottom: "1px dashed #d9d9d9",
                    display: "inline-block",
                    minHeight: 12,
                  }}
                />
              </div>
            ))}
          </div>
        ) : (
          <div
            style={{
              height: 56,
              borderRadius: 4,
              background: "#fafafa",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "#bfbfbf",
            }}
          >
            {page.kind === "dashboard"
              ? "看板预览"
              : page.kind === "flow"
                ? "流程向导（步骤条）"
                : "预览不可用"}
          </div>
        )}
      </div>
      <div style={{ marginTop: 8, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <Tooltip title="完整页面编辑器在后续里程碑；当前支持改标题。">
          <Button size="small" icon={<EditOutlined />} onClick={onAdjust}>
            调整
          </Button>
        </Tooltip>
        {!preview?.rows && page.kind === "crud_list" && <CloseOutlined style={{ color: "#d9d9d9" }} />}
      </div>
    </div>
  );
}