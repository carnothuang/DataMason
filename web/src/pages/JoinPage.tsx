import { useEffect, useRef, useState } from "react";
import { Button, Result, Spin } from "antd";
import { useNavigate } from "react-router-dom";
import { ShareApi } from "../api";

interface Props {
  token: string;
}

/** M3-5 分享链接进入页（DR-04）：进入即绑定为该部署成员（按链接档位），随后进入工作台。
 *  需已登录——未登录由 App 重定向到登录页并携带 ?from=/join/{token}，登录后回到此处生效。 */
export default function JoinPage({ token }: Props) {
  const navigate = useNavigate();
  const [error, setError] = useState("");
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    ShareApi.accept(token)
      .then((r) => {
        window.location.replace(r.entry_path || "/deployments");
      })
      .catch((e) => setError((e as Error).message));
  }, [token, navigate]);

  if (error) {
    return (
      <Result
        status="warning"
        title="无法加入"
        subTitle={error}
        extra={
          <Button type="primary" onClick={() => navigate("/deployments")}>
            返回控制台
          </Button>
        }
      />
    );
  }
  return (
    <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center" }}>
      <Spin tip="正在加入…" size="large" />
    </div>
  );
}