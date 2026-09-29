import {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
  type ReactNode,
} from "react";

export type ThemeMode = "light" | "dark";
export type ThemePreference = ThemeMode | "system";

const STORAGE_KEY = "datamason:theme";

interface ThemeContextValue {
  mode: ThemeMode;
  preference: ThemePreference;
  setPreference: (p: ThemePreference) => void;
  toggle: () => void;
  isDark: boolean;
}

const ThemeContext = createContext<ThemeContextValue>({
  mode: "light",
  preference: "system",
  setPreference: () => {},
  toggle: () => {},
  isDark: false,
});

function systemMode(): ThemeMode {
  return window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

function loadPreference(): ThemePreference {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (v === "light" || v === "dark" || v === "system") return v;
  } catch {
    // localStorage unavailable — fall through to system
  }
  return "system";
}

function resolveMode(pref: ThemePreference): ThemeMode {
  return pref === "system" ? systemMode() : pref;
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [preference, setPreferenceState] =
    useState<ThemePreference>(loadPreference);
  const [mode, setMode] = useState<ThemeMode>(() => {
    const initial = resolveMode(loadPreference());
    // 首帧前同步写入 data-theme，避免明暗主题闪烁，并让 antd token 能读到正确品牌色
    if (typeof document !== "undefined") {
      document.documentElement.setAttribute("data-theme", initial);
    }
    return initial;
  });

  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const handler = () => {
      if (preference === "system") setMode(mq.matches ? "dark" : "light");
    };
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, [preference]);

  useEffect(() => {
    setMode(resolveMode(preference));
    try {
      localStorage.setItem(STORAGE_KEY, preference);
    } catch {
      // ignore persistence failures
    }
  }, [preference]);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", mode);
  }, [mode]);

  const setPreference = useCallback((p: ThemePreference) => {
    setPreferenceState(p);
  }, []);

  const toggle = useCallback(() => {
    setPreferenceState((prev) => {
      const current = resolveMode(prev);
      return current === "light" ? "dark" : "light";
    });
  }, []);

  return (
    <ThemeContext.Provider
      value={{
        mode,
        preference,
        setPreference,
        toggle,
        isDark: mode === "dark",
      }}
    >
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  return useContext(ThemeContext);
}
