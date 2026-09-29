import { useEffect, useMemo, useState } from "react";
import GridLayout, { Layout } from "react-grid-layout";
import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";
import {
  Alert,
  App,
  Button,
  Checkbox,
  Col,
  Empty,
  Input,
  Modal,
  Radio,
  Row,
  Select,
  Space,
  Spin,
  Tag,
  Typography,
} from "antd";
import {
  DeleteOutlined,
  EyeOutlined,
  PlusOutlined,
  SaveOutlined,
  UndoOutlined,
} from "@ant-design/icons";
import type { LayoutBlockProps, PageLayoutBlock, TableMeta } from "../../api";
import { CrudApi, LayoutApi } from "../../api";

const { Text } = Typography;

interface Props {
  deploymentId: number;
  pageId: number;
  title: string;
  open: boolean;
  onClose(): void;
  onSaved(): void;
}

/** 页面布局画布设计器（0.15.0 M9-7 / DR-52/53）：
 *  react-grid-layout 管区块自由拖拽/缩放坐标；区块配置自研属性面板（渲染层与 craft.js 解耦）。
 *  保存写 `page_specs.layout_json`；`"{}"`=默认派生（零配置零变化）。 */
export default function LayoutDesigner({
  deploymentId,
  pageId,
  title,
  open,
  onClose,
  onSaved,
}: Props) {
  const { message } = App.useApp();
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [degraded, setDegraded] = useState(false);
  const [blocks, setBlocks] = useState<PageLayoutBlock[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [metas, setMetas] = useState<Record<string, TableMeta>>({});

  useEffect(() => {
    if (!open) return;
    let alive = true;
    setLoading(true);
    setSelected(null);
    Promise.all([
      LayoutApi.getPageLayout(deploymentId, pageId).catch(() => null),
      CrudApi.meta(deploymentId).catch(() => ({})),
    ])
      .then(([layout, meta]) => {
        if (!alive) return;
        setMetas(meta || {});
        const blocks0 = layout?.blocks || [];
        setBlocks(
          blocks0.length
            ? blocks0.map((b) => ({ ...b, layout: b.layout ?? { x: 0, y: 0, w: 12, h: 4 } }))
            : [],
        );
        setDegraded(layout?.degraded ?? false);
      })
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [open, deploymentId, pageId]);

  const layoutItems: Layout[] = useMemo(
    () =>
      blocks.map((b, i) => ({
        i: b.key,
        x: b.layout?.x ?? 0,
        y: b.layout?.y ?? i * 4,
        w: b.layout?.w ?? 12,
        h: b.layout?.h ?? 4,
        minW: 2,
        minH: 2,
      })),
    [blocks],
  );

  const patchProps = (key: string, changes: Partial<LayoutBlockProps>) =>
    setBlocks((prev) =>
      prev.map((b) => (b.key === key ? { ...b, props: { ...b.props, ...changes } } : b)),
    );

  const addBlock = (component: PageLayoutBlock["x_component"]) => {
    const maxN = blocks.reduce((m, b) => {
      const mm = /^b(\d+)$/.exec(b.key);
      return mm ? Math.max(m, Number(mm[1])) : m;
    }, 0);
    const key = `b${maxN + 1}`;
    const base: PageLayoutBlock = {
      key,
      x_component: component,
      props: {},
      layout: { x: 0, y: blocks.length * 4, w: component === "html" ? 12 : 6, h: 4 },
    };
    if (component === "stat") base.props = { aggregation: "count", title: "统计" };
    if (component === "html") base.props = { content: "## 说明文字" };
    setBlocks((prev) => [...prev, base]);
    setSelected(key);
  };

  const removeBlock = (key: string) => {
    setBlocks((prev) => prev.filter((b) => b.key !== key));
    setSelected(null);
  };

  const submit = async () => {
    if (!blocks.length) {
      message.error("请至少添加一个区块");
      return;
    }
    setSaving(true);
    try {
      await LayoutApi.savePageLayout(deploymentId, pageId, {
        version: 1,
        blocks: blocks.map((b) => ({
          key: b.key,
          x_component: b.x_component,
          props: b.props,
          layout: b.layout,
        })),
      });
      message.success("页面布局已保存");
      onSaved();
      onClose();
    } catch (e) {
      message.error((e as Error).message || "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const reset = async () => {
    setSaving(true);
    try {
      await LayoutApi.resetPageLayout(deploymentId, pageId);
      setBlocks([]);
      setSelected(null);
      message.success("已还原默认布局");
      onSaved();
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const selectedBlock = blocks.find((b) => b.key === selected) || null;
  const tableNames = Object.keys(metas);

  const renderBlockBody = (b: PageLayoutBlock) => {
    if (b.x_component === "table") {
      const tname = b.props.table || "";
      return (
        <div>
          <Text strong>列表表格</Text>
          <div style={{ marginTop: 4 }}>
            <Text type="secondary">
              表：{tname || "（未选择）"} · 列：{b.props.columns?.length ?? "全部"}
            </Text>
          </div>
        </div>
      );
    }
    if (b.x_component === "stat") {
      return (
        <div>
          <Text strong>统计卡</Text>
          <div style={{ marginTop: 4, fontSize: 22, fontWeight: 600 }}>
            {b.props.title || "统计"}
          </div>
          <Text type="secondary">
            {b.props.aggregation?.toUpperCase?.()}
            {b.props.column ? `(${b.props.column})` : ""}
          </Text>
        </div>
      );
    }
    if (b.x_component === "html") {
      return (
        <div>
          <Text strong>自定义内容</Text>
          <Text type="secondary" style={{ display: "block", marginTop: 4 }}>
            {b.props.content || "（空）"}
          </Text>
        </div>
      );
    }
    return (
      <div>
        <Text strong>详情</Text>
        <Text type="secondary" style={{ display: "block", marginTop: 4 }}>
          表：{b.props.table || "（未选择）"}
        </Text>
      </div>
    );
  };

  const blockKindLabel: Record<string, string> = {
    table: "表格",
    stat: "统计卡",
    html: "内容",
    detail: "详情",
  };

  return (
    <Modal
      title={`页面布局「${title}」`}
      open={open}
      onOk={submit}
      onCancel={onClose}
      okText="保存布局"
      cancelText="取消"
      confirmLoading={saving}
      width={1200}
      style={{ top: 24 }}
      destroyOnClose
    >
      {loading ? (
        <div style={{ textAlign: "center", padding: 48 }}>
          <Spin tip="加载布局…" />
        </div>
      ) : (
        <>
          {degraded && (
            <Alert
              type="warning"
              showIcon
              style={{ marginBottom: 12 }}
              message="已保存的布局部分失效，已按默认展示；请重新保存以覆盖"
            />
          )}
          <Space wrap style={{ marginBottom: 12 }}>
            <Text strong>区块</Text>
            {(["table", "stat", "html", "detail"] as const).map((c) => (
              <Button
                key={c}
                size="small"
                icon={<PlusOutlined />}
                onClick={() => addBlock(c)}
              >
                添加{blockKindLabel[c]}
              </Button>
            ))}
            <Button size="small" danger icon={<UndoOutlined />} onClick={reset}>
              还原默认
            </Button>
          </Space>

          <Row gutter={16}>
            <Col span={16}>
              <div
                style={{
                  border: "1px solid var(--fn-border-primary)",
                  borderRadius: 8,
                  padding: 8,
                  minHeight: 360,
                  background: "var(--fn-bg-elevated)",
                }}
              >
                {blocks.length === 0 ? (
                  <Empty
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                    description="从左侧「区块」添加表格 / 统计卡 / 内容到页面"
                    style={{ padding: 80 }}
                  />
                ) : (
                  <GridLayout
                    className="dm-layout-canvas"
                    layout={layoutItems}
                    cols={12}
                    rowHeight={40}
                    width={720}
                    margin={[8, 8]}
                    onLayoutChange={(next) => {
                      const byKey = new Map(next.map((l) => [l.i, l]));
                      setBlocks((prev) =>
                        prev.map((b) => {
                          const l = byKey.get(b.key);
                          return l
                            ? { ...b, layout: { x: l.x, y: l.y, w: l.w, h: l.h } }
                            : b;
                        }),
                      );
                    }}
                  >
                    {blocks.map((b) => (
                      <div
                        key={b.key}
                        onClick={() => setSelected(b.key)}
                        style={{
                          border:
                            selected === b.key
                              ? "2px solid var(--fn-color-primary)"
                              : "1px solid var(--fn-border-primary)",
                          borderRadius: 8,
                          padding: 8,
                          cursor: "pointer",
                          overflow: "hidden",
                          background: "var(--fn-bg-container)",
                          position: "relative",
                        }}
                      >
                        {selected === b.key && (
                          <Button
                            size="small"
                            type="text"
                            danger
                            icon={<DeleteOutlined />}
                            style={{ position: "absolute", top: 2, right: 2, zIndex: 2 }}
                            onClick={(e) => {
                              e.stopPropagation();
                              removeBlock(b.key);
                            }}
                          />
                        )}
                        {renderBlockBody(b)}
                      </div>
                    ))}
                  </GridLayout>
                )}
              </div>
            </Col>
            <Col span={8}>
              <Text strong style={{ display: "block", marginBottom: 8 }}>
                区块属性
              </Text>
              {selectedBlock ? (
                <div
                  style={{
                    border: "1px solid var(--fn-border-primary)",
                    borderRadius: 8,
                    padding: 12,
                  }}
                >
                  {selectedBlock.x_component === "table" && (
                    <Space direction="vertical" style={{ width: "100%" }}>
                      <span>
                        数据表
                        <Select
                          style={{ width: "100%", marginTop: 4 }}
                          placeholder="选择表"
                          value={selectedBlock.props.table}
                          options={tableNames.map((t) => ({ value: t, label: t }))}
                          onChange={(v) => patchProps(selectedBlock.key, { table: v })}
                        />
                      </span>
                      {selectedBlock.props.table && (
                        <>
                          <span>
                            显示列（留空 = 全部）
                            <div style={{ marginTop: 4 }}>
                              <Checkbox.Group
                                value={selectedBlock.props.columns || []}
                                options={(metas[selectedBlock.props.table]?.columns || [])
                                  .filter((c) => !c.is_ownership)
                                  .map((c) => ({ value: c.name, label: c.biz_name || c.name }))}
                                onChange={(v) =>
                                  patchProps(selectedBlock.key, { columns: v as string[] })
                                }
                              />
                            </div>
                          </span>
                          <span>
                            默认视图
                            <Radio.Group
                              style={{ marginLeft: 8 }}
                              value={selectedBlock.props.default_view ?? "table"}
                              onChange={(e) =>
                                patchProps(selectedBlock.key, { default_view: e.target.value })
                              }
                            >
                              <Radio.Button value="table">表格</Radio.Button>
                              <Radio.Button value="card">卡片</Radio.Button>
                            </Radio.Group>
                          </span>
                          <span>
                            行操作按钮
                            <Radio.Group
                              style={{ marginLeft: 8 }}
                              value={selectedBlock.props.show_actions !== false}
                              onChange={(e) =>
                                patchProps(selectedBlock.key, {
                                  show_actions: e.target.value,
                                })
                              }
                            >
                              <Radio.Button value={true}>显示</Radio.Button>
                              <Radio.Button value={false}>隐藏</Radio.Button>
                            </Radio.Group>
                          </span>
                        </>
                      )}
                    </Space>
                  )}

                  {selectedBlock.x_component === "stat" && (
                    <Space direction="vertical" style={{ width: "100%" }}>
                      <span>
                        数据表
                        <Select
                          style={{ width: "100%", marginTop: 4 }}
                          placeholder="选择表"
                          value={selectedBlock.props.table}
                          options={tableNames.map((t) => ({ value: t, label: t }))}
                          onChange={(v) => patchProps(selectedBlock.key, { table: v })}
                        />
                      </span>
                      <span>
                        标题
                        <Input
                          style={{ marginTop: 4 }}
                          value={selectedBlock.props.title}
                          placeholder="如：订单总额"
                          onChange={(e) =>
                            patchProps(selectedBlock.key, { title: e.target.value })
                          }
                        />
                      </span>
                      <span>
                        统计口径
                        <Select
                          style={{ width: "100%", marginTop: 4 }}
                          value={selectedBlock.props.aggregation ?? "count"}
                          options={[
                            { value: "count", label: "总数（count）" },
                            { value: "sum", label: "求和（sum）" },
                            { value: "avg", label: "平均（avg）" },
                          ]}
                          onChange={(v) =>
                            patchProps(selectedBlock.key, { aggregation: v })
                          }
                        />
                      </span>
                      {(selectedBlock.props.aggregation === "sum" ||
                        selectedBlock.props.aggregation === "avg") && (
                        <span>
                          目标列
                          <Select
                            style={{ width: "100%", marginTop: 4 }}
                            placeholder="选择数值列"
                            value={selectedBlock.props.column || undefined}
                            options={(metas[selectedBlock.props.table || ""]?.columns || [])
                              .filter((c) => ["INTEGER", "BIGINT", "DECIMAL", "NUMERIC"].includes(c.type))
                              .map((c) => ({ value: c.name, label: c.biz_name || c.name }))}
                            onChange={(v) =>
                              patchProps(selectedBlock.key, { column: v })
                            }
                          />
                        </span>
                      )}
                    </Space>
                  )}

                  {selectedBlock.x_component === "html" && (
                    <Input.TextArea
                      rows={5}
                      placeholder="支持 Markdown / 纯文本"
                      value={selectedBlock.props.content}
                      onChange={(e) =>
                        patchProps(selectedBlock.key, { content: e.target.value })
                      }
                    />
                  )}

                  {selectedBlock.x_component === "detail" && (
                    <Space direction="vertical">
                      <span>
                        数据表
                        <Select
                          style={{ width: "100%", marginTop: 4 }}
                          placeholder="选择表"
                          value={selectedBlock.props.table}
                          options={tableNames.map((t) => ({ value: t, label: t }))}
                          onChange={(v) => patchProps(selectedBlock.key, { table: v })}
                        />
                      </span>
                      <Tag>详情字段布局请在「表单配置」中调整</Tag>
                    </Space>
                  )}

                  <Button
                    size="small"
                    danger
                    type="link"
                    style={{ marginTop: 8 }}
                    onClick={() => removeBlock(selectedBlock.key)}
                  >
                    移除该区块
                  </Button>
                </div>
              ) : (
                <Empty
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                  description="点击画布中的区块以编辑属性"
                />
              )}
            </Col>
          </Row>

          <Space style={{ marginTop: 12 }}>
            <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={submit}>
              保存布局
            </Button>
            <Button icon={<EyeOutlined />} onClick={onClose}>
              取消
            </Button>
          </Space>
        </>
      )}
    </Modal>
  );
}
