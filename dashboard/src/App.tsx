import { useEffect, useState } from "react";
import { Button, Result, Spin } from "antd";
import { Navigate, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import MainLayout from "./layouts/MainLayout";
import Login, { loadCachedUser } from "./pages/Login";
import Setup from "./pages/Setup";
import Workspace from "./pages/workspace/Workspace";
import PublicPage from "./pages/PublicPage";
import JoinPage from "./pages/JoinPage";
import { AuthApi, CurrentUser, EntryApi } from "./api";

export default function App() {
  const location = useLocation();

  const [user, setUser] = useState<CurrentUser | null>(() => loadCachedUser());
  const [bootChecking, setBootChecking] = useState(true);

  useEffect(() => {
    let alive = true;
    // M3-5：访客公开页匿名访问，无需探测会话（避免 401 触发的登录跳转）
    if (typeof window !== "undefined" && window.location.pathname.startsWith("/public/")) {
      setBootChecking(false);
      return () => {
        alive = false;
      };
    }
    AuthApi.me()
      .then((me) => {
        if (!alive) return;
        setUser(me);
        sessionStorage.setItem("datamason_me", JSON.stringify(me));
      })
      .catch(() => {
        if (!alive) return;
        setUser(null);
        sessionStorage.removeItem("datamason_me");
      })
      .finally(() => alive && setBootChecking(false));
    return () => {
      alive = false;
    };
  }, []);

  // 登录页单独渲染（不带侧栏）
  if (location.pathname === "/login") {
    if (bootChecking) return <SpinPage />;
    if (user) return <Navigate to="/deployments" replace />;
    return <Login />;
  }

  // M1 首次启动向导（界面版，DR-10）：单独渲染，不带侧栏
  if (location.pathname === "/setup") {
    if (bootChecking) return <SpinPage />;
    if (user) return <Navigate to="/deployments" replace />;
    return <Setup />;
  }

  // M3-5 访客公开页（DR-04）：匿名可访问，无需登录，也不带任何菜单（只剩提交表单）
  const publicMatch = location.pathname.match(/^\/public\/(\d+)\/(\d+)\/?$/);
  if (publicMatch) {
    return <PublicPage deploymentId={Number(publicMatch[1])} pageId={Number(publicMatch[2])} />;
  }

  // M3-5 分享链接（DR-04）：进入即绑定为该部署成员（需登录，未登录先跳登录并携带来源）
  const joinMatch = location.pathname.match(/^\/join\/([^/]+)\/?$/);
  if (joinMatch) {
    if (bootChecking) return <SpinPage />;
    if (!user) {
      return <Navigate to={`/login?from=${encodeURIComponent(location.pathname)}`} replace />;
    }
    return <JoinPage token={decodeURIComponent(joinMatch[1])} />;
  }

  // 工作台运行时 /app/{id}：全屏渲染部署的菜单+页面（仍要求登录）
  const workspaceId = parseWorkspaceId(location.pathname);
  if (workspaceId !== null) {
    if (bootChecking) return <SpinPage />;
    if (!user) {
      const target = `/login?from=${encodeURIComponent(location.pathname)}`;
      return <Navigate to={target} replace />;
    }
    return <Workspace deploymentId={workspaceId} />;
  }

  // 其他页面：未登录跳登录页
  if (bootChecking) return <SpinPage />;
  if (!user) {
    const target = `/login?from=${encodeURIComponent(location.pathname)}`;
    return <Navigate to={target} replace />;
  }

  // M4 审计：设置页仅平台管理员可见（后端已 403，此处同步裁剪）
  if (location.pathname.startsWith("/settings") && !user.is_platform_admin) {
    return <Navigate to="/deployments" replace />;
  }

  // M3-2 自定义入口：非系统路由的路径 → 查 entry_path；找到渲染对应工作台（DR-15）
  const knownRoot =
    location.pathname === "/" ||
    location.pathname.startsWith("/deployments") ||
    location.pathname.startsWith("/quick-deploy") ||
    location.pathname.startsWith("/settings");
  if (!knownRoot) {
    return <CustomEntry path={location.pathname} />;
  }

  return <MainLayout user={user} />;
}

function SpinPage() {
  const { t } = useTranslation();
  return (
    <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center" }}>
      <Spin tip={t("common.loading")} />
    </div>
  );
}

/** 从 /app/{id}… 提取部署 ID；非工作台路径返回 null。 */
function parseWorkspaceId(pathname: string): number | null {
  const m = pathname.match(/^\/app\/(\d+)/);
  if (!m) return null;
  const id = Number(m[1]);
  return Number.isInteger(id) && id > 0 ? id : null;
}

/** M3-2 自定义入口解析：匹配到 entry_path → 渲染对应部署工作台（保持原 URL）；否则提示入口已失效。 */
function CustomEntry({ path }: { path: string }) {
  const { t } = useTranslation();
  const [state, setState] = useState<"loading" | "found" | "missing">("loading");
  const [deploymentId, setDeploymentId] = useState<number | null>(null);

  useEffect(() => {
    let alive = true;
    setState("loading");
    EntryApi.lookup(path)
      .then((r) => {
        if (!alive) return;
        if (r.found && r.deployment_id != null) {
          setDeploymentId(r.deployment_id);
          setState("found");
        } else {
          setState("missing");
        }
      })
      .catch(() => alive && setState("missing"));
    return () => {
      alive = false;
    };
  }, [path]);

  if (state === "loading") return <SpinPage />;
  if (state === "found" && deploymentId != null) return <Workspace deploymentId={deploymentId} />;

  return (
    <Result
      status="404"
      title={t("entry.notFoundTitle")}
      subTitle={t("entry.notFoundSubtitle")}
      extra={
        <Button type="primary" onClick={() => window.location.replace("/deployments")}>
          {t("entry.backToConsole")}
        </Button>
      }
    />
  );
}
