import { useCallback, useEffect, useState } from "react";
import {
  App,
  Button,
  Checkbox,
  Empty,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Spin,
  Tag,
  Typography,
} from "antd";
import { DeleteOutlined, LinkOutlined, ShareAltOutlined } from "@ant-design/icons";
import { ShareApi, ShareLinksPayload } from "../api";

const { Text, Paragraph } = Typography;

interface Props {
  deploymentId: number | null;
  bizName: string;
  open: boolean;
  onClose: () => void;
}

const ROLE_LABEL: Record<string, string> = { admin: "管理员", operator: "操作员" };

/** M3-5 分享弹窗（创建人独有 DR-14）：生成绑定身份的访问链接（对方登录后进入即成为成员），
 *  并给出访客公开页链接（匿名可提交）。 */
export default function ShareModal({ deploymentId, bizName, open, onClose }: Props) {
  const { message } = App.useApp();
  const [data, setData] = useState<ShareLinksPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [role, setRole] = useState<"admin" | "operator">("operator");
  const [menuIds, setMenuIds] = useState<number[]>([]);
  const [creating, setCreating] = useState(false);
  const [freshToken, setFreshToken] = useState("");

  const origin = typeof window !== "undefined" ? window.location.origin : "";

  const load = useCallback(async () => {
    if (deploymentId == null) return;
    setLoading(true);
    try {
      setData(await ShareApi.list(deploymentId));
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [deploymentId, message]);

  useEffect(() => {
    if (open) {
      setFreshToken("");
      setRole("operator");
      setMenuIds([]);
      load();
    }
  }, [open, load]);

  const create = async () => {
    if (deploymentId == null) return;
    if (role === "operator" && menuIds.length === 0) {
      message.error("请为该操作员链接勾选至少一个功能菜单");
      return;
    }
    setCreating(true);
    try {
      const link = await ShareApi.create(deploymentId, {
        role,
        menu_ids: role === "operator" ? menuIds : undefined,
      });
      setFreshToken(link.token);
      message.success("已生成分享链接");
      await load();
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setCreating(false);
    }
  };

  const revoke = async (linkId: number) => {
    if (deploymentId == null) return;
    try {
      await ShareApi.revoke(deploymentId, linkId);
      message.success("已撤销，链接立即失效");
      await load();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  return (
    <Modal
      title={
        <Space>
          <ShareAltOutlined />
          分享「{bizName}」
        </Space>
      }
      open={open}
      onCancel={onClose}
      footer={null}
      width={620}
      destroyOnClose
    >
      <Paragraph type="secondary" style={{ fontSize: 12 }}>
        分享链接会<Text strong>绑定身份</Text>：对方用账号登录后打开链接，即按所选档位加入这套系统
        （不再由你代录）。分享为创建人独有。
      </Paragraph>

      <Space direction="vertical" style={{ width: "100%" }} size={12}>
        <Space wrap>
          <Text strong>档位</Text>
          <Select
            style={{ width: 140 }}
            value={role}
            onChange={(v) => setRole(v)}
            options={[
              { value: "operator", label: "操作员（指定功能）" },
              { value: "admin", label: "管理员（全部功能）" },
            ]}
          />
          <Button type="primary" icon={<LinkOutlined />} loading={creating} onClick={create}>
            生成链接
          </Button>
        </Space>

        {role === "operator" && (
          <div>
            <Text strong style={{ display: "block", marginBottom: 6 }}>
              分给对方的菜单
            </Text>
            {data?.menus.length ? (
              <Checkbox.Group
                value={menuIds}
                onChange={(v) => setMenuIds(v as number[])}
                options={data.menus.map((m) => ({ value: m.id, label: m.label }))}
              />
            ) : (
              <Text type="secondary" style={{ fontSize: 12 }}>
                该部署还没有菜单（先在「界面与菜单」搭建）
              </Text>
            )}
          </div>
        )}

        {freshToken && (
          <div style={{ background: "#f6ffed", border: "1px solid #b7eb8f", borderRadius: 6, padding: 10 }}>
            <Text strong style={{ display: "block", marginBottom: 4 }}>
              新链接（发给对方，登录后打开）
            </Text>
            <Text code copyable={{ text: `${origin}/join/${freshToken}` }}>
              {`${origin}/join/${freshToken}`}
            </Text>
          </div>
        )}

        <div>
          <Text strong>已生成的链接</Text>
          {loading ? (
            <div style={{ textAlign: "center", padding: 16 }}>
              <Spin />
            </div>
          ) : data?.links.length ? (
            <Space direction="vertical" style={{ width: "100%", marginTop: 8 }} size={6}>
              {data.links.map((l) => (
                <Space key={l.id} wrap style={{ width: "100%", justifyContent: "space-between" }}>
                  <Space>
                    <Tag color={l.role === "admin" ? "purple" : "blue"}>
                      {ROLE_LABEL[l.role] || l.role}
                    </Tag>
                    <Text code copyable={{ text: `${origin}/join/${l.token}` }}>
                      {`${origin}/join/${l.token}`}
                    </Text>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {l.created_by_display} · {l.created_at}
                    </Text>
                  </Space>
                  <Popconfirm
                    title="撤销该链接？"
                    description="撤销后链接立即失效。"
                    onConfirm={() => revoke(l.id)}
                  >
                    <Button size="small" danger icon={<DeleteOutlined />}>
                      撤销
                    </Button>
                  </Popconfirm>
                </Space>
              ))}
            </Space>
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无分享链接" style={{ marginTop: 8 }} />
          )}
        </div>

        {data && data.public_pages.length > 0 && (
          <div>
            <Text strong>访客公开页（无需登录，仅可提交）</Text>
            <Space direction="vertical" style={{ width: "100%", marginTop: 8 }} size={6}>
              {data.public_pages.map((p) => {
                const url = `${origin}/public/${deploymentId}/${p.id}`;
                return (
                  <Space key={p.id}>
                    <Text>{p.title}</Text>
                    <Text code copyable={{ text: url }}>
                      {url}
                    </Text>
                  </Space>
                );
              })}
            </Space>
          </div>
        )}

        {/* 保留一个只读输入以便手动全选复制（部分浏览器 copyable 受限时兜底） */}
        {freshToken && (
          <Input readOnly value={`${origin}/join/${freshToken}`} onFocus={(e) => e.target.select()} />
        )}
      </Space>
    </Modal>
  );
}