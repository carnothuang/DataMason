import { useCallback, useEffect, useState } from "react";
import {
  App,
  Button,
  Card,
  Empty,
  Space,
  Spin,
  Tag,
  Typography,
} from "antd";
import { PlusOutlined, ReloadOutlined, TableOutlined } from "@ant-design/icons";
import { DeployApi, Deployment } from "../api";
import DeployWizard from "./deploy/DeployWizard";
import DataViewer from "./deploy/DataViewer";

const { Title, Text, Paragraph } = Typography;

const STATUS_COLOR: Record<string, string> = {
  running: "green",
  deploying: "blue",
  stopped: "default",
  draft: "default",
  destroyed: "red",
};
const STATUS_LABEL: Record<string, string> = {
  running: "运行中",
  deploying: "部署中",
  stopped: "已停止",
  draft: "草稿",
  destroyed: "已销毁",
};

export default function Deployments() {
  const { message } = App.useApp();
  const [rows, setRows] = useState<Deployment[]>([]);
  const [loading, setLoading] = useState(false);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [openId, setOpenId] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRows(await DeployApi.list());
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => {
    load();
  }, [load]);

  const current = rows.find((r) => r.id === openId);
  if (current) {
    return (
      <DataViewer
        deploymentId={current.id}
        bizName={current.biz_name}
        onBack={() => setOpenId(null)}
      />
    );
  }

  return (
    <div>
      <Space style={{ width: "100%", justifyContent: "space-between", marginBottom: 16 }}>
        <Title level={4} style={{ margin: 0 }}>
          我部署过的系统
        </Title>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={load}>
            刷新
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setWizardOpen(true)}>
            部署一套系统
          </Button>
        </Space>
      </Space>

      <Spin spinning={loading}>
        {rows.length === 0 && !loading ? (
          <Card>
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={
                <Space direction="vertical" size={2}>
                  <Text type="secondary">还没有部署任何系统</Text>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    先在「设置 → 模型管理」配置并测试一个 LLM，然后点右上角「部署一套系统」
                  </Text>
                </Space>
              }
            />
          </Card>
        ) : (
          <Space direction="vertical" style={{ width: "100%" }} size={12}>
            {rows.map((d) => (
              <Card
                key={d.id}
                size="small"
                hoverable
                onClick={() => setOpenId(d.id)}
                styles={{ body: { padding: 16 } }}
              >
                <Space style={{ width: "100%", justifyContent: "space-between" }} align="start">
                  <div>
                    <Space>
                      <Text strong style={{ fontSize: 15 }}>
                        {d.biz_name}
                      </Text>
                      <Tag color={STATUS_COLOR[d.status] || "default"}>
                        {STATUS_LABEL[d.status] || d.status}
                      </Tag>
                      {d.entry_path && (
                        <Text code style={{ fontSize: 12 }}>
                          {d.entry_path}
                        </Text>
                      )}
                    </Space>
                    <Paragraph
                      type="secondary"
                      ellipsis={{ rows: 1 }}
                      style={{ margin: "6px 0 0", maxWidth: 560 }}
                    >
                      {d.purpose}
                    </Paragraph>
                  </div>
                  <Space onClick={(e) => e.stopPropagation()}>
                    <Button
                      size="small"
                      icon={<TableOutlined />}
                      onClick={() => setOpenId(d.id)}
                    >
                      打开数据
                    </Button>
                  </Space>
                </Space>
              </Card>
            ))}
          </Space>
        )}
      </Spin>

      <DeployWizard
        open={wizardOpen}
        onClose={() => setWizardOpen(false)}
        onDeployed={load}
      />
    </div>
  );
}
