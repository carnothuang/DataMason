"""配置中心服务：多模型 / 数据连接 / Redis。

安全：
- LLM api_key、数据库连接串一律经 Cipher 加密后落库，读取时掩码。
- 测试连接只做最小探测（SELECT 1 / PING），不做任何写操作（sqlite 建文件除外）。
M1 说明：登录会话未上线前，这些接口仅在本机/局域网暴露；接入认证后加权限保护。
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote_plus

from sqlalchemy import create_engine, text

from server.models import AppSetting, Connection, LlmModel

REDIS_SETTING_KEY = "redis"

DEFAULT_REDIS = {
    "enabled": False,
    "host": "127.0.0.1",
    "port": 6379,
    "db": 0,
    "password": "",
}

MASK = "********"


def _session(app):
    return app.extensions["datamason_session_factory"]()


def _cipher(app):
    return app.extensions["cipher"]


# ---------------- LLM 多模型 ----------------

def list_models(app) -> list[dict]:
    with _session(app) as s:
        rows = s.query(LlmModel).order_by(LlmModel.id).all()
        return [_model_public(m, has_key=bool(m.api_key_cipher)) for m in rows]


def _model_public(m: LlmModel, has_key: bool) -> dict:
    return {
        "id": m.id,
        "name": m.name,
        "base_url": m.base_url,
        "model_name": m.model_name,
        "temperature": m.temperature,
        "is_default": m.is_default,
        "has_api_key": has_key,
        "api_key": MASK if has_key else "",
    }


def create_model(app, data: dict) -> dict:
    cipher = _cipher(app)
    with _session(app) as s:
        m = LlmModel(
            name=data["name"].strip(),
            base_url=data["base_url"].strip(),
            model_name=data.get("model_name", "").strip(),
            temperature=float(data.get("temperature", 0.0)),
            api_key_cipher=cipher.encrypt(data.get("api_key", "")),
            is_default=False,
        )
        s.add(m)
        s.flush()
        if s.query(LlmModel).count() == 1:  # 首个模型自动设默认
            m.is_default = True
        result = _model_public(m, has_key=bool(m.api_key_cipher))
        s.commit()
        return result


def update_model(app, model_id: int, data: dict) -> dict:
    cipher = _cipher(app)
    with _session(app) as s:
        m = s.get(LlmModel, model_id)
        if m is None:
            raise KeyError("模型不存在")
        for field in ("name", "base_url", "model_name"):
            if field in data and data[field] is not None:
                setattr(m, field, str(data[field]).strip())
        if "temperature" in data and data["temperature"] is not None:
            m.temperature = float(data["temperature"])
        # api_key：空或掩码表示不变；仅当传入新值才替换
        key = data.get("api_key", "")
        if key and key != MASK:
            m.api_key_cipher = cipher.encrypt(key)
        result = _model_public(m, has_key=bool(m.api_key_cipher))
        s.commit()
        return result


def delete_model(app, model_id: int) -> None:
    with _session(app) as s:
        m = s.get(LlmModel, model_id)
        if m is None:
            raise KeyError("模型不存在")
        s.delete(m)
        s.commit()


def set_default_model(app, model_id: int) -> dict:
    with _session(app) as s:
        target = s.get(LlmModel, model_id)
        if target is None:
            raise KeyError("模型不存在")
        for m in s.query(LlmModel).all():
            m.is_default = m.id == model_id
        s.commit()
        return _model_public(target, has_key=bool(target.api_key_cipher))


def test_model(app, model_id: int) -> dict:
    """真实调用一次 chat/completions，返回模型回复或错误。"""
    from server.llm.client import LlmConfig, LlmNotConfiguredError, OpenAiCompatibleClient

    cipher = _cipher(app)
    with _session(app) as s:
        m = s.get(LlmModel, model_id)
        if m is None:
            raise KeyError("模型不存在")
        cfg = LlmConfig(
            base_url=m.base_url,
            api_key=cipher.decrypt(m.api_key_cipher),
            model=m.model_name or m.name,
            temperature=m.temperature,
            timeout=20,
        )
    try:
        client = OpenAiCompatibleClient(cfg)
        _parsed, usage = client.chat_json(
            "你是连通性测试助手，只输出 JSON。",
            '只返回 JSON：{"ok": true}',
        )
        return {"ok": True, "reply": _parsed}
    except LlmNotConfiguredError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


# ---------------- 数据连接 ----------------

def _build_sqlite_uri(db_path: str, data_root: str) -> tuple[str, str]:
    """返回 (SQLAlchemy URI, 落盘绝对路径)。相对路径锚定数据根目录。"""
    p = Path(db_path)
    if not p.is_absolute():
        p = Path(data_root) / db_path
    p.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{p.as_posix()}", str(p)


def _build_mysql_uri(data: dict, with_db: bool = True) -> str:
    user = quote_plus(data.get("user", "root"))
    pw = quote_plus(data.get("password", ""))
    host = data.get("host", "127.0.0.1")
    port = int(data.get("port", 3306))
    base = f"mysql+pymysql://{user}:{pw}@{host}:{port}"
    db = (data.get("database") or "").strip()
    return f"{base}/{db}" if with_db and db else base


def list_connections(app) -> list[dict]:
    with _session(app) as s:
        rows = s.query(Connection).order_by(Connection.id).all()
        return [_connection_public(c) for c in rows]


def _connection_public(c: Connection) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "dialect": c.dialect,
        "status": c.status,
        # 不回传连接串/密码，前端只展示方言与状态
    }


def create_connection(app, data: dict) -> dict:
    cipher = _cipher(app)
    dialect = data["dialect"]
    name = data["name"].strip()
    create_now = bool(data.get("create", False))

    if dialect == "sqlite":
        uri, _abs = _build_sqlite_uri(data.get("db_path", f"{name}.db"),
                                      app.config["DATA_ROOT"])
        if create_now:
            engine = create_engine(uri, future=True)
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))  # 连接即创建文件
            engine.dispose()
    elif dialect == "mysql":
        # 保存时不强制带库（可指向实例，建库走部署管线）；测试用带库/不带库均可
        uri = _build_mysql_uri(data, with_db=bool(data.get("database")))
    elif dialect == "postgresql":
        user = quote_plus(data.get("user", "postgres"))
        pw = quote_plus(data.get("password", ""))
        uri = (f"postgresql+psycopg2://{user}:{pw}@"
               f"{data.get('host', '127.0.0.1')}:{int(data.get('port', 5432))}"
               f"/{data.get('database', '').strip()}")
    else:
        raise ValueError(f"不支持的方言: {dialect}")

    with _session(app) as s:
        c = Connection(name=name, dialect=dialect,
                       conn_uri=cipher.encrypt(uri), status="unknown")
        s.add(c)
        s.flush()
        result = _connection_public(c)
        s.commit()
        return result


def delete_connection(app, conn_id: int) -> None:
    with _session(app) as s:
        c = s.get(Connection, conn_id)
        if c is None:
            raise KeyError("连接不存在")
        s.delete(c)
        s.commit()


def test_connection(app, conn_id: int) -> dict:
    cipher = _cipher(app)
    with _session(app) as s:
        c = s.get(Connection, conn_id)
        if c is None:
            raise KeyError("连接不存在")
        uri = cipher.decrypt(c.conn_uri)
        dialect = c.dialect
        cid = c.id
    try:
        engine = create_engine(uri, future=True, connect_args={"connect_timeout": 5}
                               if dialect != "sqlite" else {})
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        status = "ok"
        ok, error = True, None
    except Exception as exc:  # noqa: BLE001
        status = "error"
        ok, error = False, f"{type(exc).__name__}: {exc}"
    with _session(app) as s:
        row = s.get(Connection, cid)
        if row:
            row.status = status
            s.commit()
    return {"ok": ok, "status": status, "error": error}


# ---------------- Redis ----------------

def get_redis_config(app) -> dict:
    with _session(app) as s:
        row = s.get(AppSetting, REDIS_SETTING_KEY)
        if row is None:
            return dict(DEFAULT_REDIS)
        cfg = json.loads(row.value_json)
    merged = dict(DEFAULT_REDIS)
    merged.update(cfg)
    merged["password"] = MASK if merged.get("password") else ""
    return merged


def _get_raw_redis(app) -> dict:
    with _session(app) as s:
        row = s.get(AppSetting, REDIS_SETTING_KEY)
        cfg = dict(DEFAULT_REDIS)
        if row:
            cfg.update(json.loads(row.value_json))
        return cfg


def save_redis_config(app, data: dict) -> dict:
    with _session(app) as s:
        current = dict(DEFAULT_REDIS)
        row = s.get(AppSetting, REDIS_SETTING_KEY)
        if row:
            current.update(json.loads(row.value_json))
        for key in ("enabled", "host", "port", "db"):
            if key in data:
                current[key] = data[key]
        pw = data.get("password", MASK)
        if pw and pw != MASK:  # 掩码表示保持原密码
            current["password"] = pw
        if "enabled" in data and not data["enabled"]:
            current["enabled"] = False
        if row is None:
            row = AppSetting(key=REDIS_SETTING_KEY, value_json=json.dumps(current))
            s.add(row)
        else:
            row.value_json = json.dumps(current)
        s.commit()
    public = get_redis_config(app)
    return public


def test_redis(app) -> dict:
    import redis as redis_lib

    cfg = _get_raw_redis(app)
    try:
        client = redis_lib.Redis(
            host=cfg["host"], port=int(cfg["port"]), db=int(cfg["db"]),
            password=cfg["password"] or None, socket_connect_timeout=5,
        )
        pong = client.ping()
        info = client.info("server")
        return {"ok": bool(pong), "version": info.get("redis_version")}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def make_redis_client(app):
    """供业务层按需取用；未启用返回 None。"""
    import redis as redis_lib

    cfg = _get_raw_redis(app)
    if not cfg.get("enabled"):
        return None
    return redis_lib.Redis(
        host=cfg["host"], port=int(cfg["port"]), db=int(cfg["db"]),
        password=cfg["password"] or None, socket_connect_timeout=3,
    )
