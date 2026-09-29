import { useCallback, useEffect, useState } from "react";
import { App, Select, Space, Statistic, Typography } from "antd";
import { AiGenApi, AiGenerationsPage } from "../../api";
import { GenerationTable } from "../settings/AiGenerationsTab";

const { Text } = Typography;

interface Props {
  deploymentId: number;
}

/** M4-4 工作台工程室「AI 生成」：本部署生成历史（创建人/管理员，只读）。 */
export default function DeploymentGenerationsPage({ deploymentId }: Props) {
  const { message } = App.useApp();
  const [data, setData] = useState<AiGenerationsPage | null>(null);
  const [loading, setLoading] = useState(false);
  const [kind, setKind] = useState<string | undefined>();
  const [status, setStatus] = useState<string | undefined>();

  const load = useCallback(() => {
    setLoading(true);
    AiGenApi.forDeployment(deploymentId, { kind, status, page_size: 50 })
      .then(setData)
      .catch((e) => message.error((e as Error).message))
      .finally(() => setLoading(false));
  }, [deploymentId, kind, status, message]);

  useEffect(() => {
    load();
  }, [load]);

  const stats = data?.stats;
  return (
    <div>
      <Space wrap style={{ marginBottom: 12 }}>
        <Select
          allowClear
          placeholder="全部类型"
          style={{ width: 150 }}
          value={kind}
          onChange={setKind}
          options={[
            { value: "deploy", label: "部署提案" },
            { value: "seed", label: "种子数据" },
            { value: "crud", label: "直连补标签" },
            { value: "qa", label: "问数据" },
          ]}
        />
        <Select
          allowClear
          placeholder="全部状态"
          style={{ width: 120 }}
          value={status}
          onChange={setStatus}
          options={[
            { value: "ok", label: "成功" },
            { value: "error", label: "失败" },
          ]}
        />
      </Space>
      <Space size="large" style={{ marginBottom: 12 }} wrap>
        <Statistic title="记录数" value={stats?.total ?? 0} />
        <Statistic title="输入 token" value={stats?.tokens_in ?? 0} />
        <Statistic title="输出 token" value={stats?.tokens_out ?? 0} />
        <Statistic title="平均耗时(ms)" value={stats?.avg_duration_ms ?? 0} />
      </Space>
      <Text type="secondary" style={{ display: "block", marginBottom: 8, fontSize: 12 }}>
        本部署的 AI 生成历史（只读）；清理与全量治理见「设置 → AI 生成」。
      </Text>
      <GenerationTable data={data} loading={loading} />
    </div>
  );
}
