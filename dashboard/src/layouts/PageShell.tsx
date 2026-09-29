import type { ReactNode } from "react";
import { Segmented, Tabs, Typography } from "antd";

const { Title, Text } = Typography;

export interface PathTabOption {
  value: string;
  label: string;
  icon?: ReactNode;
}

export interface PathTabsConfig {
  value: string;
  options: PathTabOption[];
  onChange: (value: string | number) => void;
}

interface PageShellProps {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
  pathTabs?: PathTabsConfig;
  fill?: boolean;
  children: ReactNode;
}

function useIsMobile(): boolean {
  return typeof window !== "undefined" && window.innerWidth <= 767;
}

function PageShell({
  title,
  subtitle,
  actions,
  pathTabs,
  fill,
  children,
}: PageShellProps) {
  const isMobile = useIsMobile();
  const outerPad = isMobile ? 12 : 32;
  const outerPadTop = isMobile ? 12 : 24;
  const contentPad = isMobile ? 12 : 24;
  const pinBody = Boolean(fill || (isMobile && pathTabs));

  const titleActions =
    !isMobile && pathTabs ? (
      <>
        <Segmented
          size="middle"
          value={pathTabs.value}
          onChange={pathTabs.onChange}
          options={pathTabs.options.map((opt) => ({
            value: opt.value,
            label: (
              <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                {opt.icon}
                {opt.label}
              </span>
            ),
          }))}
        />
        {actions}
      </>
    ) : (
      actions
    );

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        padding: `${outerPadTop}px ${outerPad}px ${outerPad}px`,
        boxSizing: "border-box",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "flex-start",
          justifyContent: "space-between",
          gap: 12,
          flexShrink: 0,
          marginBottom: 24,
        }}
      >
        <div>
          <Title
            level={4}
            style={{
              margin: 0,
              lineHeight: "28px",
              fontSize: 20,
              fontWeight: 600,
            }}
          >
            {title}
          </Title>
          {subtitle && (
            <Text
              type="secondary"
              style={{ fontSize: 13, marginTop: 4, display: "block" }}
            >
              {subtitle}
            </Text>
          )}
        </div>
        {titleActions && (
          <div style={{ flexShrink: 0, paddingTop: 2 }}>{titleActions}</div>
        )}
      </div>

      <div
        style={{
          flex: 1,
          background: "var(--fn-bg-elevated)",
          border: "1px solid var(--fn-border-primary)",
          borderRadius: 8,
          padding: contentPad,
          overflowX: pinBody || isMobile ? "hidden" : "auto",
          overflowY: pinBody ? "hidden" : "auto",
          minHeight: 0,
          minWidth: 0,
          display: pinBody ? "flex" : undefined,
          flexDirection: pinBody ? "column" : undefined,
        }}
      >
        {isMobile && pathTabs && (
          <div style={{ marginBottom: 12 }}>
            <Segmented
              block
              size="small"
              value={pathTabs.value}
              onChange={pathTabs.onChange}
              options={pathTabs.options.map((opt) => ({
                value: opt.value,
                label: opt.label,
              }))}
            />
          </div>
        )}
        {children}
      </div>
    </div>
  );
}

type FillTabsProps = Omit<PageShellProps, "fill" | "children"> &
  Omit<Parameters<typeof Tabs>[0], "items"> & {
    items: NonNullable<Parameters<typeof Tabs>[0]["items"]>;
  };

function PageShellFillTabs({
  title,
  subtitle,
  actions,
  pathTabs,
  ...tabsProps
}: FillTabsProps) {
  const isMobile = useIsMobile();
  return (
    <PageShell
      title={title}
      subtitle={subtitle}
      actions={actions}
      pathTabs={pathTabs}
      fill={!isMobile}
    >
      <Tabs
        {...tabsProps}
        style={{
          flex: 1,
          minHeight: 0,
          display: "flex",
          flexDirection: "column",
        }}
      />
    </PageShell>
  );
}

PageShell.FillTabs = PageShellFillTabs;

export default PageShell;
export type { PageShellProps };
