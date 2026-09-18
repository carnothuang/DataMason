import { useCallback, useEffect, useState } from "react";
import {
  App,
  Button,
  Card,
  Collapse,
  Dropdown,
  Empty,
  Space,
  Spin,
  Tag,
  Typography,
} from "antd";
import { PlayCircleOutlined, PlusOutlined, ProjectOutlined, ReloadOutlined, ShareAltOutlined, TableOutlined, ThunderboltOutlined, DownloadOutlined, DeleteOutlined, DownOutlined } from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import { DeployApi, Deployment, downloadJson } from "../api";
import DeployWizard from "./deploy/DeployWizard";
import DataViewer from "./deploy/DataViewer";
import BlueprintPage from "./deploy/BlueprintPage";
import ShareModal from "./ShareModal";
import DestroyModal from "./DestroyModal";

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
  const { message, modal } = App.useApp();
  const navigate = useNavigate();
  const [rows, setRows] = useState<Deployment[]>([]);
  const [loading, setLoading] = useState(false);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [wizardBuilder, setWizardBuilder] = useState<"auto" | "hybrid">("auto");
  const [openId, setOpenId] = useState<number | null>(null);
  const [blueprintId, setBlueprintId] = useState<number | null>(null);
  const [shareDep, setShareDep] = useState<Deployment | null>(null);
  const [destroyDep, setDestroyDep] = useState<Deployment | null>(null);
  const [exportingId, setExportingId] = useState<number | null>(null);

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

  // 入口下拉：全自动（简单业务）/ 混合引导（复杂需求）二选一，进入部署向导的对应模式
  const openWizard = (builder: "auto" | "hybrid") => {
    setWizardBuilder(builder);
    setWizardOpen(true);
  };

  // M3-1：清单分组 —— 运行中/进行中铺开，已停止默认折叠（DR：运行中铺开 / 已停止折叠）
  const runningRows = rows.filter((r) => ["running", "deploying", "draft"].includes(r.status) || !["running", "stopped"].includes(r.status));
  const stoppedRows = rows.filter((r) => r.status === "stopped");

  const onStop = (d: Deployment) => {
    modal.confirm({
      title: `停止「${d.biz_name}」？`,
      content: "停止后数据保留、工作台入口不可操作；可随时再启动恢复。",
      okText: "停止",
      okButtonProps: { danger: true },
      cancelText: "取消",
      onOk: async () => {
        try {
          await DeployApi.stop(d.id);
          message.success("已停止");
          load();
        } catch (e) {
          message.error((e as Error).message);
        }
      },
    });
  };

  const onStart = async (d: Deployment) => {
    try {
      await DeployApi.start(d.id);
      message.success("已再启动");
      load();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  // M3-6：导出配置包（含全量操作日志），可离线打开；停止态也可导出
  const onExport = async (d: Deployment) => {
    setExportingId(d.id);
    try {
      const pkg = await DeployApi.exportPackage(d.id);
      downloadJson(`datamason-deploy-${d.id}.json`, pkg);
      message.success("已导出配置包");
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setExportingId(null);
    }
  };

  // DR-08：一键清空演示数据（只删种子行，不动用户真实数据）
  const onClearSeed = (d: Deployment) => {
    modal.confirm({
      title: `清空「${d.biz_name}」的演示数据？`,
      content: "只删除部署时自动生成的演示行，之后录入的数据不受影响。此操作不可撤销。",
      okText: "清空演示数据",
      okButtonProps: { danger: true },
      cancelText: "取消",
      onOk: async () => {
        try {
          const r = await DeployApi.clearSeed(d.id);
          message.success(`已清空演示数据（${r.deleted} 行）`);
          load();
        } catch (e) {
          message.error((e as Error).message);
        }
      },
    });
  };

  const current = rows.find((r) => r.id === openId);
  const blueprint = rows.find((r) => r.id === blueprintId);
  if (blueprint) {
    return (
      <BlueprintPage
        deploymentId={blueprint.id}
        bizName={blueprint.biz_name}
        dataMode={blueprint.data_mode}
        onBack={() => setBlueprintId(null)}
      />
    );
  }
  if (current) {
    return (
      <DataViewer
        deploymentId={current.id}
        bizName={current.biz_name}
        dataMode={current.data_mode}
        onBack={() => setOpenId(null)}
        onOpenBlueprint={() => setBlueprintId(current.id)}
      />
    );
  }

  const renderCard = (d: Deployment) => (
    <Card
      key={d.id}
      size="small"
      hoverable
      onClick={() => setOpenId(d.id)}
      styles={{ body: { padding: 16 } }}
      style={d.status === "stopped" ? { opacity: 0.92, background: "#fafafa" } : undefined}
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
            {d.data_mode === "link" && <Tag color="orange">直连</Tag>}
            {d.entry_path && (
              <Text
                code
                copyable={{ text: `${window.location.origin}${d.entry_path}` }}
                style={{ fontSize: 12 }}
              >
                {`${window.location.origin}${d.entry_path}`}
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
          {d.status === "running" && d.entry_path && (
            <Button
              type="primary"
              size="small"
              ghost
              icon={<ThunderboltOutlined />}
              onClick={() => navigate(d.entry_path)}
            >
              打开工作台
            </Button>
          )}
          <Button
            size="small"
            icon={<TableOutlined />}
            onClick={() => setOpenId(d.id)}
          >
            打开数据
          </Button>
          <Button
            size="small"
            icon={<ProjectOutlined />}
            onClick={() => setBlueprintId(d.id)}
          >
            数据蓝图
          </Button>
          {d.role === "creator" && (
            <Button
              size="small"
              icon={<ShareAltOutlined />}
              onClick={() => setShareDep(d)}
            >
              分享
            </Button>
          )}
          {(d.role === "creator" || d.role === "admin") && (
            <Button
              size="small"
              icon={<DownloadOutlined />}
              loading={exportingId === d.id}
              onClick={() => onExport(d)}
            >
              导出
            </Button>
          )}
          {d.seed_data && (d.role === "creator" || d.role === "admin") && (
            <Button size="small" onClick={() => onClearSeed(d)}>
              清空演示
            </Button>
          )}
          {d.status === "running" && d.role === "creator" && (
            <Button size="small" danger onClick={() => onStop(d)}>
              停止
            </Button>
          )}
          {d.status === "stopped" && d.role === "creator" && (
            <Button size="small" icon={<PlayCircleOutlined />} onClick={() => onStart(d)}>
              再启动
            </Button>
          )}
          {d.role === "creator" && (
            <Button size="small" danger icon={<DeleteOutlined />} onClick={() => setDestroyDep(d)}>
              销毁
            </Button>
          )}
        </Space>
      </Space>
    </Card>
  );

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
          <Dropdown
            menu={{
              items: [
                { key: "auto", label: "全自动（简单业务）" },
                { key: "hybrid", label: "混合引导（复杂需求）" },
              ],
              onClick: ({ key }) => openWizard(key as "auto" | "hybrid"),
            }}
          >
            <Button type="primary" icon={<PlusOutlined />}>
              部署一套系统 <DownOutlined style={{ fontSize: 12 }} />
            </Button>
          </Dropdown>
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
          <Space direction="vertical" style={{ width: "100%" }} size={16}>
            <div>
              {runningRows.length > 0 && (
                <Text type="secondary" style={{ display: "block", marginBottom: 8 }}>
                  运行中（{runningRows.length}）
                </Text>
              )}
              <Space direction="vertical" style={{ width: "100%" }} size={12}>
                {runningRows.map(renderCard)}
              </Space>
            </div>
            {stoppedRows.length > 0 && (
              <Collapse
                ghost
                style={{ borderTop: "1px solid #f0f0f0" }}
                items={[
                  {
                    key: "stopped",
                    label: `已停止（${stoppedRows.length}）— 数据已保留，可再启动`,
                    children: (
                      <Space direction="vertical" style={{ width: "100%" }} size={12}>
                        {stoppedRows.map(renderCard)}
                      </Space>
                    ),
                  },
                ]}
              />
            )}
          </Space>
        )}
      </Spin>

      <DeployWizard
        open={wizardOpen}
        initialBuilder={wizardBuilder}
        onClose={() => setWizardOpen(false)}
        onDeployed={load}
      />

      <ShareModal
        deploymentId={shareDep?.id ?? null}
        bizName={shareDep?.biz_name ?? ""}
        open={shareDep !== null}
        onClose={() => setShareDep(null)}
      />

      <DestroyModal
        deployment={destroyDep}
        open={destroyDep !== null}
        onClose={() => setDestroyDep(null)}
        onDestroyed={load}
      />
    </div>
  );
}