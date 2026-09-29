import { useEffect, useState } from "react";
import {
  App,
  Button,
  Form,
  Input,
  InputNumber,
  Spin,
  Switch,
} from "antd";
import {
  ApiOutlined,
  CheckCircleFilled,
  CloseCircleFilled,
  ThunderboltOutlined,
} from "@ant-design/icons";
import { RedisApi, RedisConfig } from "../../api";
import { useTranslation } from "react-i18next";
import "./shared.css";

export default function RedisTab() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [form] = Form.useForm<RedisConfig>();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{
    ok: boolean;
    text: string;
  } | null>(null);

  useEffect(() => {
    RedisApi.get()
      .then((cfg) => form.setFieldsValue(cfg))
      .catch((e) => message.error(e.message))
      .finally(() => setLoading(false));
  }, [form, message]);

  const enabled = Form.useWatch("enabled", form);

  const save = async () => {
    const values = await form.validateFields();
    setSaving(true);
    try {
      await RedisApi.save(values);
      message.success(t("redis.saveSuccess"));
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const test = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const values = await form.validateFields();
      await RedisApi.save(values);
      const r = await RedisApi.test();
      if (r.ok) {
        setTestResult({
          ok: true,
          text: t("redis.testSuccess", { version: r.version ?? "" }),
        });
      } else {
        setTestResult({ ok: false, text: r.error || t("redis.testFail") });
      }
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setTesting(false);
    }
  };

  if (loading) {
    return (
      <div className="dm-set-page">
        <div className="dm-set-grid-loading">
          <Spin />
        </div>
      </div>
    );
  }

  return (
    <div className="dm-set-page">
      <div className="dm-set-page-top">
        <div className="dm-set-legend" role="note">
          <span className="dm-set-legend-label">
            {t("redis.legendTitle")}
          </span>
          <span className="dm-set-legend-text">{t("redis.legend")}</span>
        </div>
      </div>

      <Form
        form={form}
        layout="vertical"
        requiredMark={false}
        initialValues={{ enabled: false, port: 6379, db: 0 }}
        className="dm-set-form dm-set-config-form"
      >
        <div className="dm-set-section dm-set-config-switch-row">
          <div className="dm-set-card-avatar" aria-hidden="true">
            <ThunderboltOutlined />
          </div>
          <div className="dm-set-config-switch-text">
            <div className="dm-set-config-switch-title">
              {t("redis.formEnabled")}
            </div>
          </div>
          <Form.Item name="enabled" valuePropName="checked" noStyle>
            <Switch />
          </Form.Item>
        </div>

        <div
          className="dm-set-config-fields"
          style={{ opacity: enabled ? 1 : 0.5 }}
          aria-disabled={!enabled}
        >
          <div className="dm-set-section">
            <div className="dm-set-section-title">
              {t("redis.sectionConn")}
            </div>
            <div className="dm-set-form-row dm-set-form-row-redis">
              <Form.Item
                name="host"
                label={t("redis.formHost")}
                rules={
                  enabled
                    ? [{ required: true, message: t("redis.hostRequired") }]
                    : []
                }
              >
                <Input placeholder="127.0.0.1" disabled={!enabled} />
              </Form.Item>
              <Form.Item
                name="port"
                label={t("redis.formPort")}
                rules={
                  enabled
                    ? [{ required: true, message: t("redis.portRequired") }]
                    : []
                }
              >
                <InputNumber
                  min={1}
                  max={65535}
                  style={{ width: "100%" }}
                  disabled={!enabled}
                />
              </Form.Item>
              <Form.Item name="db" label={t("redis.formDb")}>
                <InputNumber
                  min={0}
                  max={15}
                  style={{ width: "100%" }}
                  disabled={!enabled}
                />
              </Form.Item>
            </div>
            <Form.Item name="password" label={t("redis.formPassword")}>
              <Input.Password
                autoComplete="new-password"
                disabled={!enabled}
              />
            </Form.Item>
          </div>
        </div>

        <div className="dm-set-config-actions">
          <Button type="primary" loading={saving} onClick={save}>
            {t("redis.save")}
          </Button>
          <Button icon={<ApiOutlined />} loading={testing} onClick={test}>
            {t("redis.saveAndTest")}
          </Button>
          {testResult && (
            <span
              className={`dm-set-config-result ${
                testResult.ok
                  ? "dm-set-config-result-ok"
                  : "dm-set-config-result-error"
              }`}
            >
              {testResult.ok ? <CheckCircleFilled /> : <CloseCircleFilled />}
              {testResult.text}
            </span>
          )}
        </div>
      </Form>
    </div>
  );
}
