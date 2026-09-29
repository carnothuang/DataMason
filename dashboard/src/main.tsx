import React from "react";
import ReactDOM from "react-dom/client";
import { ConfigProvider, App as AntApp, theme as antdTheme } from "antd";
import zhCN from "antd/locale/zh_CN";
import enUS from "antd/locale/en_US";
import { useTranslation } from "react-i18next";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { ThemeProvider, useTheme } from "./context/ThemeContext";
import { cssVar } from "./styles/tokens";
import "./i18n";
import "antd/dist/reset.css";
import "./styles/theme.css";

function Root() {
  const { isDark } = useTheme();
  const { i18n } = useTranslation();
  const antdLocale = i18n.language?.startsWith("zh") ? zhCN : enUS;
  return (
    <ConfigProvider
      locale={antdLocale}
      theme={{
        algorithm: isDark
          ? antdTheme.darkAlgorithm
          : antdTheme.defaultAlgorithm,
        token: {
          colorPrimary: cssVar(
            "--fn-color-brand",
            isDark ? "#a7b0b9" : "#4c545e",
          ),
          borderRadius: 8,
          fontFamily:
            '-apple-system, BlinkMacSystemFont, "Segoe UI", "Inter", "Noto Sans SC", sans-serif',
        },
      }}
    >
      <AntApp>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </AntApp>
    </ConfigProvider>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ThemeProvider>
      <Root />
    </ThemeProvider>
  </React.StrictMode>,
);
