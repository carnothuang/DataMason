import { useEffect, useState } from "react";
import { Button, Input, App as AntApp } from "antd";
import { LockOutlined, UserOutlined } from "@ant-design/icons";
import { useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { AuthApi, CurrentUser } from "../api";
import { useTheme } from "../context/ThemeContext";
import SlideCaptcha from "../components/SlideCaptcha";

export default function Login() {
  const location = useLocation();
  const { t } = useTranslation();
  const { isDark } = useTheme();
  const { message } = AntApp.useApp();
  const [loading, setLoading] = useState<"login" | "bootstrap" | null>(null);
  const [needsSetup, setNeedsSetup] = useState<boolean | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [captchaReady, setCaptchaReady] = useState(false);
  const [captchaResetKey, setCaptchaResetKey] = useState(0);

  useEffect(() => {
    AuthApi.setupStatus()
      .then((s) => {
        setNeedsSetup(s.needs_setup);
      })
      .catch(() => setNeedsSetup(false));
  }, []);

  const params = new URLSearchParams(location.search);
  const from = params.get("from") || "/deployments";

  const resetCaptcha = () => {
    setCaptchaReady(false);
    setCaptchaResetKey((k) => k + 1);
  };

  const onLogin = async () => {
    if (!username || !password || !captchaReady) return;
    setLoading("login");
    try {
      const me = await AuthApi.login({ username, password });
      sessionStorage.setItem("datamason_me", JSON.stringify(me));
      message.success(t("login.welcome", { name: me.display_name }));
      window.location.replace(from);
    } catch (err) {
      message.error((err as Error).message);
      resetCaptcha();
    } finally {
      setLoading(null);
    }
  };

  const onBootstrap = async () => {
    setLoading("bootstrap");
    try {
      const res = await AuthApi.bootstrap();
      if (res.skipped) {
        message.info(t("login.bootstrapExists"));
      } else if (Array.isArray(res.created) && res.created.length > 0) {
        message.success(t("login.bootstrapCreated", { names: res.created.join(", ") }));
      } else {
        message.info(t("login.bootstrapDone"));
      }
      setNeedsSetup(false);
    } catch (err) {
      message.error((err as Error).message);
    } finally {
      setLoading(null);
    }
  };

  return (
    <div
      style={{
        minHeight: "100dvh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "var(--fn-bg-layout)",
        transition: "background var(--fn-transition)",
      }}
    >
      <div
        style={{
          width: "100%",
          maxWidth: 360,
          padding: "48px 32px 40px",
          background: "var(--fn-bg-elevated)",
          borderRadius: 16,
          boxShadow: "0 8px 32px rgba(0,0,0,0.08)",
          border: "1px solid var(--fn-border-primary)",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 20,
          margin: "0 16px",
        }}
      >
        <img
          src={isDark ? "/logo_name_dark.png" : "/logo_name.png"}
          alt="DataMason"
          style={{
            height: 44,
            width: "auto",
            maxWidth: 260,
            objectFit: "contain",
            display: "block",
          }}
        />

        <h2
          style={{
            fontSize: 20,
            fontWeight: 600,
            color: "var(--fn-text-primary)",
            margin: 0,
            textAlign: "center",
          }}
        >
          {t("login.heading")}
        </h2>

        <Input
          prefix={<UserOutlined style={{ color: "var(--fn-text-quaternary)" }} />}
          placeholder={t("login.username")}
          size="large"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoFocus
          autoComplete="username"
          style={{ borderRadius: 10 }}
        />

        <Input.Password
          prefix={<LockOutlined style={{ color: "var(--fn-text-quaternary)" }} />}
          placeholder={t("login.password")}
          size="large"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          onPressEnter={onLogin}
          autoComplete="current-password"
          style={{ borderRadius: 10 }}
        />

        <SlideCaptcha
          hint={t("login.slideHint")}
          verifiedLabel={t("login.slideVerified")}
          onVerified={() => setCaptchaReady(true)}
          resetKey={captchaResetKey}
        />

        <Button
          type="primary"
          size="large"
          block
          loading={loading === "login"}
          onClick={onLogin}
          disabled={!username || !password || !captchaReady}
          style={{ borderRadius: 10, height: 44, fontWeight: 500 }}
        >
          {t("login.submit")}
        </Button>

        {needsSetup && (
          <>
            <Button type="link" block onClick={() => window.location.assign("/setup")}>
              {t("login.firstRunSetup")}
            </Button>
            <Button
              type="link"
              block
              onClick={onBootstrap}
              loading={loading === "bootstrap"}
            >
              {t("login.bootstrapFromEnv")}
            </Button>
          </>
        )}
      </div>
    </div>
  );
}

export function loadCachedUser(): CurrentUser | null {
  try {
    const raw = sessionStorage.getItem("datamason_me");
    if (!raw) return null;
    const parsed = JSON.parse(raw) as CurrentUser;
    if (parsed && typeof parsed.id === "number") return parsed;
    sessionStorage.removeItem("datamason_me");
    return null;
  } catch {
    return null;
  }
}
