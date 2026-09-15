import { Button, Card, Empty, Space, Tag, Typography } from "antd";
import { PlusOutlined, ReloadOutlined } from "@ant-design/icons";

const { Title, Text } = Typography;

// M1-1：首页=部署清单。M1-2 接入部署管线后改为真实数据（GET /api/v1/deployments）。
export default function Deployments() {
  return (
    <div>
      <Space style={{ width: "100%", justifyContent: "space-between", marginBottom: 16 }}>
        <Title level={4} style={{ margin: 0 }}>
          我部署过的系统
        </Title>
        <Space>
          <Button icon={<ReloadOutlined />}>刷新</Button>
          <Button type="primary" icon={<PlusOutlined />} disabled>
            部署一套系统
          </Button>
        </Space>
      </Space>

      <Card>
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={
            <Space direction="column" size={2}>
              <Text type="secondary">还没有部署任何系统</Text>
              <Text type="secondary" style={{ fontSize: 12 }}>
                「部署一套系统」将在部署管线（M1-2）完成后开放
              </Text>
            </Space>
          }
        />
      </Card>

      <div style={{ marginTop: 16 }}>
        <Tag color="orange">M0 已完成</Tag>
        <Tag color="blue">M1 进行中</Tag>
        <Text type="secondary" style={{ fontSize: 12 }}>
          先在「设置」里配置 LLM 模型与数据连接，再开始部署。
        </Text>
      </div>
    </div>
  );
}
