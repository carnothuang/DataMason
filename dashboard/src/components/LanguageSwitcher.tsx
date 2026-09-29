import { useTranslation } from "react-i18next";
import { Tooltip } from "antd";

const LANGUAGES = [
  { key: "zh", label: "中" },
  { key: "en", label: "EN" },
] as const;

/**
 * 中/EN 语言切换（对齐 Mason 标准）。语言存入 localStorage，
 * 由 `src/i18n.ts` 的 languageChanged 监听持久化并同步 <html lang>。
 */
export default function LanguageSwitcher() {
  const { i18n, t } = useTranslation();
  const current = i18n.language?.startsWith("zh") ? "zh" : "en";

  return (
    <div
      role="group"
      aria-label={t("account.language")}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 2,
        padding: 3,
        borderRadius: "var(--fn-radius-full, 999px)",
        background: "var(--fn-bg-tertiary)",
      }}
    >
      {LANGUAGES.map(({ key, label }) => {
        const active = current === key;
        return (
          <Tooltip key={key} title={key === "zh" ? "简体中文" : "English"} mouseEnterDelay={0.4}>
            <button
              type="button"
              onClick={() => void i18n.changeLanguage(key)}
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                minWidth: 28,
                height: 28,
                padding: "0 6px",
                border: "none",
                borderRadius: "var(--fn-radius-full, 999px)",
                background: active ? "var(--fn-bg-elevated)" : "transparent",
                color: active ? "var(--fn-text-primary)" : "var(--fn-text-secondary)",
                boxShadow: active ? "0 1px 3px rgba(0,0,0,0.1)" : "none",
                cursor: "pointer",
                transition: "all 0.2s ease",
                fontSize: 12,
                fontWeight: active ? 600 : 400,
              }}
            >
              {label}
            </button>
          </Tooltip>
        );
      })}
    </div>
  );
}
