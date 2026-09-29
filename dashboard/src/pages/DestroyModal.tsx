import { useEffect, useState } from "react";
import { Alert, App, Button, Checkbox, Modal, Result, Space, Tag, Typography } from "antd";
import { DeleteOutlined, DownloadOutlined, ExclamationCircleOutlined } from "@ant-design/icons";
import { Deployment, DeployApi, downloadJson } from "../api";

const { Text, Paragraph } = Typography;

interface Props {
  deployment: Deployment | null;
  open: boolean;
  onClose: () => void;
  onDestroyed: () => void;
}

/** M3-7 销毁（创建人独有 DR-14）：二次确认 + 先导出数据提示。
 *  创建模式连物理库一起清，直连模式只解除登记（绝不删外部库）；销毁后为告别态（C10）。 */
export default function DestroyModal({ deployment, open, onClose, onDestroyed }: Props) {
  const { message, modal } = App.useApp();
  const [exported, setExported] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [destroying, setDestroying] = useState(false);
  const [done, setDone] = useState(false);

  useEffect(() => {
    if (open) {
      setExported(false);
      setExporting(false);
      setConfirmed(false);
      setDestroying(false);
      setDone(false);
    }
  }, [open]);

  if (!deployment) return null;
  const d = deployment;

  const doExport = async () => {
    setExporting(true);
    try {
      const pkg = await DeployApi.exportPackage(d.id);
      downloadJson(`datamason-deploy-${d.id}.json`, pkg);
      setExported(true);
      message.success("已导出配置包（含全量操作日志）");
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setExporting(false);
    }
  };

  const doDestroy = () => {
    modal.confirm({
      title: `最后确认：销毁「${d.biz_name}」？`,
      icon: <ExclamationCircleOutlined />,
      content:
        "销毁后这套系统与操作日志将被清除，且不可恢复。" +
        (d.data_mode === "link"
          ? "直连模式只解除登记，绝不会删除你的外部数据库。"
          : "创建模式会一并删除独立数据文件。"),
      okText: "确认销毁",
      okButtonProps: { danger: true },
      cancelText: "取消",
      onOk: async () => {
        setDestroying(true);
        try {
          await DeployApi.destroy(d.id);
          setDone(true);
          onDestroyed();
        } catch (e) {
          message.error((e as Error).message);
        } finally {
          setDestroying(false);
        }
      },
    });
  };

  return (
    <Modal
      title="销毁系统"
      open={open}
      onCancel={onClose}
      footer={null}
      width={560}
      destroyOnClose
    >
      {done ? (
        <Result
          status="success"
          title={`「${d.biz_name}」已销毁`}
          subTitle="系统与操作日志已清除。若需留档，请使用此前的导出包（内含全量日志）。"
          extra={
            <Button type="primary" onClick={onClose}>
              完成
            </Button>
          }
        />
      ) : (
        <Space direction="vertical" style={{ width: "100%" }} size={14}>
          <Alert
            type="error"
            showIcon
            message="销毁不可恢复"
            description={
              <span>
                将清除「<Text strong>{d.biz_name}</Text>」的表/页面/菜单/流程/成员与
                <Text strong> 操作日志</Text>。
                {d.data_mode === "link" ? (
                  <>
                    {" "}
                    这是<Text strong>直连模式</Text>：只解除登记，
                    <Text strong>绝不删除你的外部数据库</Text>。
                  </>
                ) : (
                  <> 这是<Text strong>创建模式</Text>：独立数据文件会一并删除。</>
                )}
              </span>
            }
          />

          <div>
            <Text strong>第一步：先导出数据（建议）</Text>
            <Paragraph type="secondary" style={{ margin: "4px 0 8px", fontSize: 12 }}>
              导出包含配置与<Text strong>全量操作日志</Text>的 JSON，可离线保存留档。
            </Paragraph>
            <Space>
              <Button icon={<DownloadOutlined />} loading={exporting} onClick={doExport}>
                先导出数据
              </Button>
              {exported && <Tag color="green">已导出</Tag>}
            </Space>
          </div>

          <div>
            <Text strong>第二步：确认销毁</Text>
            <div style={{ marginTop: 8 }}>
              <Checkbox checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)}>
                我已了解后果（{d.data_mode === "link" ? "外部库保留，仅清登记" : "含数据文件一并删除"}
                ），确认销毁
              </Checkbox>
            </div>
          </div>

          <Space style={{ justifyContent: "flex-end", width: "100%" }}>
            <Button onClick={onClose}>取消</Button>
            <Button
              danger
              type="primary"
              icon={<DeleteOutlined />}
              disabled={!confirmed}
              loading={destroying}
              onClick={doDestroy}
            >
              销毁
            </Button>
          </Space>
        </Space>
      )}
    </Modal>
  );
}