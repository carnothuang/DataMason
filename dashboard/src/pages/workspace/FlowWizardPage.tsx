import { useCallback, useEffect, useMemo, useState } from "react";
import { Alert, App, Button, Card, Empty, Result, Space, Spin, Steps, Tag, Typography } from "antd";
import { ReloadOutlined, RightOutlined } from "@ant-design/icons";
import { FlowRuntime, FlowRuntimeApi, WorkspacePage } from "../../api";
import CrudListPage from "./CrudListPage";

const { Text } = Typography;

interface Props {
  deploymentId: number;
  page: WorkspacePage;
  role?: string;
  dataMode?: string;
}

/** M3-4 运行态流程向导页（DR-06）：顶部步骤条（已完成/当前/待做）+ 主区=当前步骤页面。
 *  当前步完成条件满足才可「下一步」；点下一步由服务端写状态字段 + activity_logs（状态随步推进）。 */
export default function FlowWizardPage({ deploymentId, page, role, dataMode }: Props) {
  const { message } = App.useApp();
  const [runtime, setRuntime] = useState<FlowRuntime | null>(null);
  const [loading, setLoading] = useState(true);
  const [advancing, setAdvancing] = useState(false);
  const flowId = page.flow_id ?? 0;

  const load = useCallback(async () => {
    if (!flowId) return;
    setLoading(true);
    try {
      setRuntime(await FlowRuntimeApi.get(deploymentId, flowId));
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [deploymentId, flowId, message]);

  useEffect(() => {
    void load();
  }, [load]);

  const current = useMemo(
    () => runtime?.steps.find((s) => s.state === "current") ?? null,
    [runtime],
  );

  const advance = async () => {
    setAdvancing(true);
    try {
      const res = await FlowRuntimeApi.advance(deploymentId, flowId);
      message.success(
        res.done ? `「${res.step}」完成，流程已全部完成` : `「${res.step}」完成，进入下一步`,
      );
      await load();
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setAdvancing(false);
    }
  };

  if (!page.flow_id) {
    return (
      <Card>
        <Empty description="该页面未绑定流程（数据异常），请在工程室重新编排" />
      </Card>
    );
  }
  if (loading && !runtime) {
    return (
      <div style={{ display: "flex", justifyContent: "center", padding: 48 }}>
        <Spin tip="加载流程…" />
      </div>
    );
  }
  if (!runtime) {
    return (
      <Card>
        <Empty description="流程加载失败" />
      </Card>
    );
  }

  const items = runtime.steps.map((s) => ({
    title: s.name,
    description: (
      <Text type="secondary" style={{ fontSize: 12 }}>
        {s.page_title}
      </Text>
    ),
    status: s.state === "done" ? ("finish" as const) : s.state === "current" ? ("process" as const) : ("wait" as const),
  }));

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <Card size="small">
        <Steps size="small" current={runtime.current_index} items={items} />
      </Card>

      {runtime.done ? (
        <Result
          status="success"
          title="流程已完成"
          subTitle={`「${runtime.name}」的 ${runtime.steps.length} 个步骤均已完成`}
        />
      ) : (
        <>
          {current && (
            <Alert
              type={current.met ? "success" : "warning"}
              showIcon
              message={
                <Space>
                  <Text strong>当前步骤：{current.name}</Text>
                  <Tag color={current.met ? "green" : "orange"}>
                    {current.met ? "完成条件已满足" : "完成条件未满足"}
                  </Tag>
                  {current.status_value && (
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      推进为「{current.status_value}」
                    </Text>
                  )}
                </Space>
              }
              description={
                <Text type="secondary">
                  {current.met
                    ? "点击「下一步」推进本步骤，状态随之进入下一环节。"
                    : `完成条件：${current.condition}。请先在下方页面完成操作，再点「下一步」。`}
                </Text>
              }
            />
          )}

          {current?.kind === "crud_list" && current.table ? (
            <CrudListPage
              deploymentId={deploymentId}
              table={current.table}
              tableBizName={current.table_biz_name || current.table}
              role={role}
              dataMode={dataMode}
            />
          ) : (
            <Card>
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={`当前步骤页面「${current?.page_title || ""}」（类型 ${current?.kind || "未知"}）暂无可内嵌的页面渲染`}
              />
            </Card>
          )}

          <Space>
            <Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading}>
              刷新状态
            </Button>
            <Button
              type="primary"
              icon={<RightOutlined />}
              disabled={!current?.met}
              loading={advancing}
              onClick={advance}
            >
              下一步
            </Button>
          </Space>
        </>
      )}
    </div>
  );
}