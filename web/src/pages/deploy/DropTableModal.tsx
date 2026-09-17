import { useEffect, useState } from "react";
import { Alert, App, Button, Descriptions, Input, Modal, Space, Tag, Typography } from "antd";
import { DeployApi, DropImpact } from "../../api";

const { Text } = Typography;

interface Props {
  deploymentId: number;
  table: string;
  bizName: string;
  open: boolean;
  onClose: () => void;
  onDropped: () => void;
}

/** 0.11.0 M5-A：删表影响面提案卡 + 二次确认（被引用/被流程引用则阻断）。 */
export default function DropTableModal({
  deploymentId, table, bizName, open, onClose, onDropped,
}: Props) {
  const { message } = App.useApp();
  const [impact, setImpact] = useState<DropImpact | null>(null);
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    setImpact(null);
    setConfirm("");
    DeployApi.proposeDropTable(deploymentId, table)
      .then(setImpact)
      .catch((e) => message.error(e.message));
  }, [open, deploymentId, table, message]);

  const onDrop = async () => {
    if (confirm !== table) {
      message.error("请输入完整表名以确认");
      return;
    }
    setBusy(true);
    try {
      const r = await DeployApi.dropTable(deploymentId, table);
      message.success(`已删除表 ${table}（清理 ${r.cascade.pages} 页 / ${r.cascade.menus} 菜单）`);
      onDropped();
      onClose();
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      title={`删除表 · ${bizName}（${table}）`}
      open={open}
      onCancel={onClose}
      width={620}
      footer={
        <Space>
          <Button onClick={onClose}>取消</Button>
          <Button danger disabled={!impact || impact.blocked || confirm !== table}
                  loading={busy} onClick={onDrop}>
            确认删除
          </Button>
        </Space>
      }
    >
      {!impact ? (
        <Text type="secondary">正在计算影响面…</Text>
      ) : (
        <>
          {impact.blocked ? (
            <Alert type="error" showIcon style={{ marginBottom: 12 }}
                   message="该表暂不能删除" description={impact.reason} />
          ) : (
            <Alert type="warning" showIcon style={{ marginBottom: 12 }}
                   message="删除不可恢复；建议先导出配置包/数据" />
          )}
          <Descriptions size="small" column={1} bordered>
            <Descriptions.Item label="数据行数">{impact.row_count}</Descriptions.Item>
            <Descriptions.Item label="被外键引用">
              {impact.referenced_by.length
                ? impact.referenced_by.map((r) => `${r.table}.${r.column}`).join("、")
                : "无"}
            </Descriptions.Item>
            <Descriptions.Item label="关联页面">
              {impact.pages.length ? impact.pages.map((p) => p.title || `#${p.id}`).join("、") : "无"}
            </Descriptions.Item>
            <Descriptions.Item label="关联菜单">
              {impact.menus.length ? impact.menus.map((m) => m.label).join("、") : "无"}
            </Descriptions.Item>
            <Descriptions.Item label="涉及授权">{impact.grants} 条</Descriptions.Item>
            <Descriptions.Item label="被流程引用">
              {impact.flows.length ? impact.flows.join("、") : "无"}
            </Descriptions.Item>
            <Descriptions.Item label="报表引用">
              {impact.reports.length ? impact.reports.join("、") : "无"}
            </Descriptions.Item>
            <Descriptions.Item label="演示种子行">{impact.seeds}</Descriptions.Item>
          </Descriptions>
          {!impact.blocked && (
            <div style={{ marginTop: 12 }}>
              <Text type="secondary">
                请输入表名 <Tag>{table}</Tag> 以确认删除：
              </Text>
              <Input style={{ marginTop: 6 }} value={confirm} placeholder={table}
                     onChange={(e) => setConfirm(e.target.value)} />
            </div>
          )}
        </>
      )}
    </Modal>
  );
}
