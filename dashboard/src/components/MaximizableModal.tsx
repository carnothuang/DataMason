import { ReactNode, useEffect, useState } from "react";
import { Button, Modal, ModalProps } from "antd";
import { CompressOutlined, ExpandOutlined } from "@ant-design/icons";

interface Props extends ModalProps {
  title: ReactNode;
}

/** Modal 包装：标题栏带「最大化/还原」切换；最大化时铺满可视区、内容区自适应滚动。 */
export default function MaximizableModal(props: Props) {
  const { open, title, width, style, styles, children, ...rest } = props;
  const [max, setMax] = useState(false);

  useEffect(() => {
    if (!open) setMax(false);
  }, [open]);

  return (
    <Modal
      {...rest}
      open={open}
      width={max ? "calc(100vw - 48px)" : width}
      style={{ ...(max ? { top: 24 } : {}), ...style }}
      styles={{
        ...styles,
        content: max
          ? {
              maxHeight: "calc(100vh - 48px)",
              display: "flex",
              flexDirection: "column",
              ...styles?.content,
            }
          : styles?.content,
        body: max
          ? { flex: 1, minHeight: 0, overflow: "auto", ...styles?.body }
          : styles?.body,
      }}
      title={
        <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
          {title}
          <Button
            type="text"
            size="small"
            aria-label={max ? "还原窗口" : "最大化窗口"}
            title={max ? "还原" : "最大化"}
            icon={max ? <CompressOutlined /> : <ExpandOutlined />}
            onClick={() => setMax((v) => !v)}
            style={{ marginTop: -2 }}
          />
        </span>
      }
    >
      {children}
    </Modal>
  );
}