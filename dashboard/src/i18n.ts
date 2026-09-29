import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import zh from "./locales/zh.json";
import en from "./locales/en.json";

/** Mason 标准：仅支持 zh / en，zh 为回退。 */
export const SUPPORTED_LOCALES = ["zh", "en"] as const;
export type UiLocale = (typeof SUPPORTED_LOCALES)[number];

const STORAGE_KEY = "datamason:locale";

function resolveInitialLocale(): UiLocale {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === "zh" || saved === "en") return saved;
  } catch {
    /* localStorage 不可用 */
  }
  const nav = typeof navigator !== "undefined" ? navigator.language ?? "" : "";
  return nav.toLowerCase().startsWith("zh") ? "zh" : "en";
}

void i18n.use(initReactI18next).init({
  resources: {
    zh: { translation: zh },
    en: { translation: en },
  },
  lng: resolveInitialLocale(),
  fallbackLng: "zh",
  supportedLngs: [...SUPPORTED_LOCALES],
  interpolation: { escapeValue: false },
});

function syncDocumentLang(lng: string): void {
  if (typeof document === "undefined") return;
  document.documentElement.lang = lng.startsWith("zh") ? "zh" : "en";
}

syncDocumentLang(i18n.language);

i18n.on("languageChanged", (lng) => {
  const locale: UiLocale = lng.startsWith("zh") ? "zh" : "en";
  try {
    localStorage.setItem(STORAGE_KEY, locale);
  } catch {
    /* localStorage 不可用 */
  }
  syncDocumentLang(locale);
});

export default i18n;
