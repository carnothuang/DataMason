import { useCallback, useEffect, useMemo, useState } from "react";
import {
  App,
  Alert,
  Button,
  Empty,
  Modal,
  Popconfirm,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Tooltip,
  Tree,
  Typography,
} from "antd";
import type { DataNode } from "antd/es/tree";
import { PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import { MembersApi, WorkspaceApi, WorkspaceMenu } from "../../api";
import type { DeploymentMember } from "../../api";

const { Text } = Typography;

const ROLE_LABEL: Record<string, string> = {
  creator: "创建人",
  admin: "管理员",
  operator: "操作员",
};

const ROLE_COLOR: Record<string, string> = {
  creator: "orange",
  admin: "blue",
  operator: "default",
};

interface Props {
  deploymentId: number;
  canEdit: boolean;
}

/** M2-5 成员与权限：三层角色 + 按人授权（把功能分给人，DR-02）。
 *  创建人可增删成员/改角色/勾选功能；管理员只读（DR-14）。 */
export default function MembersPage({ deploymentId, canEdit }: Props) {
  const { message } = App.useApp();
  const [data, setData] = useState<{ members: DeploymentMember[]; menus: { id: number; label: string; parent_id: number | null }[] } | null>(null);
  const [menuTree, setMenuTree] = useState<DataNode[]>([]);
  const [loading, setLoading] = useState(false);

  const [candidates, setCandidates] = useState<{ id: number; username: string; display_name: string }[]>([]);
  const [addOpen, setAddOpen] = useState(false);
  const [addUsername, setAddUsername] = useState<string | undefined>();
  const [addRole, setAddRole] = useState<"admin" | "operator">("operator");
  const [addMenuIds, setAddMenuIds] = useState<number[]>([]);

  const [editing, setEditing] = useState<DeploymentMember | null>(null);
  const [editRole, setEditRole] = useState<"admin" | "operator">("operator");
  const [editMenuIds, setEditMenuIds] = useState<number[]>([]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await MembersApi.list(deploymentId));
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [deploymentId, message]);

  useEffect(() => {
    load();
    WorkspaceApi.info(deploymentId)
      .then((info) => setMenuTree(toTree(info.menus)))
      .catch((e) => message.error(e.message));
  }, [deploymentId, load, message]);

  const menuIdToLabel = useMemo(() => {
    const map: Record<number, string> = {};
    for (const m of data?.menus || []) map[m.id] = m.label;
    return map;
  }, [data]);

  const openAdd = async () => {
    setAddUsername(undefined);
    setAddRole("operator");
    setAddMenuIds([]);
    setAddOpen(true);
    try {
      setCandidates(await MembersApi.candidates(deploymentId));
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const submitAdd = async () => {
    if (!addUsername) {
      message.error("请选择要添加的账号");
      return;
    }
    try {
      await MembersApi.add(deploymentId, {
        username: addUsername,
        role: addRole,
        menu_ids: addRole === "operator" ? addMenuIds : [],
      });
      message.success("成员已添加");
      setAddOpen(false);
      load();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const openEdit = (member: DeploymentMember) => {
    setEditing(member);
    setEditRole(member.role === "admin" ? "admin" : "operator");
    setEditMenuIds(member.menu_ids);
  };

  const submitEdit = async () => {
    if (!editing) return;
    try {
      await MembersApi.update(deploymentId, editing.user_id, {
        role: editRole,
        menu_ids:
          editRole === "operator" ? editMenuIds : editing.menu_ids,
      });
      message.success("已保存");
      setEditing(null);
      load();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const doRemove = async (member: DeploymentMember) => {
    try {
      await MembersApi.remove(deploymentId, member.user_id);
      message.success(`已移除 ${member.display_name}`);
      load();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const grantSummary = (menuIds: number[], role: string) => {
    if (role === "creator" || role === "admin") {
      return <Text type="secondary">全部功能</Text>;
    }
    if (menuIds.length === 0) {
      return <Text type="secondary">未分配功能</Text>;
    }
    const labels = menuIds.map((id) => menuIdToLabel[id]).filter(Boolean);
    return (
      <Tooltip title={labels.join("、")}>
        <Text>{labels.slice(0, 3).join("、")}{labels.length > 3 ? ` 等 ${labels.length} 项` : ""}</Text>
      </Tooltip>
    );
  };

  return (
    <div>
      <Space style={{ width: "100%", justifyContent: "space-between", marginBottom: 12 }}>
        <Text strong style={{ fontSize: 15 }}>
          成员与权限 · 把功能分给人（DR-02）
        </Text>
        <Space>
          {canEdit && (
            <Button type="primary" icon={<PlusOutlined />} onClick={openAdd}>
              添加成员
            </Button>
          )}
          <Button icon={<ReloadOutlined />} onClick={load}>
            刷新
          </Button>
        </Space>
      </Space>

      {!canEdit && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="管理员可查看成员名单；成员增删与授权变更为创建人独有（DR-14）。"
        />
      )}

      {!data ? (
        <div style={{ padding: 40, textAlign: "center" }}>
          <Spin tip="加载成员…" />
        </div>
      ) : data.members.length === 0 ? (
        <Empty description="还没有成员" />
      ) : (
        <Table
          rowKey="user_id"
          loading={loading}
          dataSource={data.members}
          pagination={false}
          size="middle"
          columns={[
            { title: "用户", dataIndex: "display_name", width: 180,
              render: (_: unknown, m: DeploymentMember) => (
                <Space>
                  <Text strong>{m.display_name}</Text>
                  <Text type="secondary" style={{ fontSize: 12 }}>@{m.username}</Text>
                  {m.is_self && <Tag color="green">我</Tag>}
                </Space>
              ) },
            {
              title: "角色",
              dataIndex: "role",
              width: 120,
              render: (r: string) => <Tag color={ROLE_COLOR[r] || "default"}>{ROLE_LABEL[r] || r}</Tag>,
            },
            {
              title: "分到的功能",
              render: (_: unknown, m: DeploymentMember) => grantSummary(m.menu_ids, m.role),
            },
            canEdit
              ? {
                  title: "操作",
                  width: 220,
                  render: (_: unknown, m: DeploymentMember) =>
                    m.role === "creator" ? (
                      <Text type="secondary">创建人</Text>
                    ) : (
                      <Space size="small">
                        <Button size="small" onClick={() => openEdit(m)}>
                          {m.role === "admin" ? "改角色" : "分配功能"}
                        </Button>
                        <Popconfirm
                          title={`移除成员 ${m.display_name}？`}
                          description="移除后该账号将无法进入本套系统"
                          okText="移除"
                          okButtonProps={{ danger: true }}
                          onConfirm={() => doRemove(m)}
                        >
                          <Button size="small" danger>
                            移除
                          </Button>
                        </Popconfirm>
                      </Space>
                    ),
                }
              : { title: "操作", render: () => null },
          ]}
        />
      )}

      {/* 添加成员 */}
      <Modal
        title="添加成员"
        open={addOpen}
        onOk={submitAdd}
        onCancel={() => setAddOpen(false)}
        okText="添加"
        cancelText="取消"
        width={520}
      >
        <Space direction="vertical" style={{ width: "100%", marginTop: 8 }}>
          <Select
            showSearch
            placeholder="选择账号（输入账号名搜索）"
            style={{ width: "100%" }}
            value={addUsername}
            onChange={(v) => setAddUsername(v as string)}
            optionFilterProp="label"
            options={candidates.map((u) => ({
              value: u.username,
              label: `${u.display_name}（${u.username}）`,
            }))}
          />
          <Select
            placeholder="角色"
            style={{ width: 180 }}
            value={addRole}
            onChange={(v) => setAddRole(v as "admin" | "operator")}
            options={[
              { value: "admin", label: "管理员（全部功能）" },
              { value: "operator", label: "操作员（指派功能）" },
            ]}
          />
          {addRole === "operator" ? (
            <div>
              <Text type="secondary" style={{ display: "block", marginBottom: 4 }}>
                勾选分给这个人的功能（留空将被拒绝，操作员需至少一个功能）
              </Text>
              <div style={{ border: "1px solid #f0f0f0", borderRadius: 8, maxHeight: 260, overflow: "auto", padding: 4 }}>
                <GrantTree
                  tree={menuTree}
                  checkedKeys={addMenuIds}
                  onChange={setAddMenuIds}
                />
              </div>
            </div>
          ) : (
            <Text type="secondary">管理员拥有本套系统全部业务功能（不含工程室/成员变更）。</Text>
          )}
        </Space>
      </Modal>

      {/* 编辑成员：改角色 / 授权变更 */}
      <Modal
        title={editing ? `编辑成员 · ${editing.display_name}` : "编辑成员"}
        open={!!editing}
        onOk={submitEdit}
        onCancel={() => setEditing(null)}
        okText="保存"
        cancelText="取消"
        width={520}
      >
        <Space direction="vertical" style={{ width: "100%", marginTop: 8 }}>
          <Select
            style={{ width: 220 }}
            value={editRole}
            onChange={(v) => setEditRole(v as "admin" | "operator")}
            options={[
              { value: "admin", label: "管理员（全部功能）" },
              { value: "operator", label: "操作员（指派功能）" },
            ]}
          />
          {editRole === "operator" ? (
            <div>
              <Text type="secondary" style={{ display: "block", marginBottom: 4 }}>
                勾选分给这个人的功能
              </Text>
              <div style={{ border: "1px solid #f0f0f0", borderRadius: 8, maxHeight: 260, overflow: "auto", padding: 4 }}>
                <GrantTree
                  tree={menuTree}
                  checkedKeys={editMenuIds}
                  onChange={setEditMenuIds}
                />
              </div>
            </div>
          ) : (
            <Text type="secondary">管理员拥有本套系统全部业务功能，无需逐项分配。</Text>
          )}
        </Space>
      </Modal>
    </div>
  );
}

function GrantTree({
  tree,
  checkedKeys,
  onChange,
}: {
  tree: DataNode[];
  checkedKeys: number[];
  onChange: (keys: number[]) => void;
}) {
  const [expanded, setExpanded] = useState<string[]>([]);
  useEffect(() => {
    const ids: string[] = [];
    const walk = (nodes: DataNode[]) => {
      for (const n of nodes) {
        ids.push(String(n.key));
        if ("children" in n && n.children) walk(n.children as DataNode[]);
      }
    };
    walk(tree);
    setExpanded(ids);
  }, [tree]);

  if (tree.length === 0) {
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="该部署暂无菜单（没有可分发的功能）" />;
  }

  return (
    <Tree
      checkable
      selectable={false}
      defaultExpandAll
      expandedKeys={expanded}
      onExpand={(keys) => setExpanded(keys as string[])}
      treeData={tree}
      checkedKeys={{ checked: checkedKeys.map(String), halfChecked: [] }}
      onCheck={(checked) => onChange((checked as string[]).map(Number))}
    />
  );
}

function toTree(menus: WorkspaceMenu[]): DataNode[] {
  return menus.map((m) => ({
    key: String(m.id),
    title: m.label,
    children: (m.children || []).length ? toTree(m.children) : undefined,
  }));
}