"""部署级 API key（M3-8，DR-11）：把部署的 CRUD REST 开放给外部系统。

- key 明文 `dm_<随机>` 仅创建时回显一次；落库 Fernet 密文（复用 Cipher），掩码回读前缀。
- 验签：按前缀定位候选行 → 解密比对（常量时间）；吊销（status=revoked）即失效。
- key 管理创建人独有（DR-14）；key 写操作在 activity_logs 以 actor_label 标记 key 名。
"""
from __future__ import annotations

import hmac
import secrets
from datetime import datetime

from datamason.audit.service import log_system
from datamason.models import DeploymentApiKey, User

KEY_PREFIX = "dm_"
_PREFIX_LEN = 10


class ApiKeyError(ValueError):
    """API key 管理校验错误 → 400。"""


def _session(app):
    return app.extensions["datamason_session_factory"]()


def _mask(raw: str) -> str:
    return f"{raw[:_PREFIX_LEN]}…"


def create_key(app, deployment_id: int, name: str, actor_user_id: int) -> dict:
    name = (name or "").strip()
    if not name or len(name) > 64:
        raise ApiKeyError("密钥名称必须为 1-64 字符")
    raw = KEY_PREFIX + secrets.token_urlsafe(32)
    with _session(app) as s:
        row = DeploymentApiKey(
            deployment_id=deployment_id,
            name=name,
            key_cipher=app.extensions["cipher"].encrypt(raw),
            key_prefix=raw[:_PREFIX_LEN],
            status="active",
            created_by=actor_user_id,
        )
        s.add(row)
        s.flush()
        key_id = row.id
        s.commit()
    log_system(
        app, deployment_id, "apikey", actor_user_id=actor_user_id,
        object_kind="apikey", object_pk=str(key_id),
        object_label=f"发放密钥「{name}」（{_mask(raw)}）",
        changes={"type": "apikey_create", "name": name, "prefix": raw[:_PREFIX_LEN]},
    )
    # 明文仅在创建响应中回显一次
    return {"id": key_id, "name": name, "key": raw, "prefix": raw[:_PREFIX_LEN], "status": "active"}


def list_keys(app, deployment_id: int) -> dict:
    with _session(app) as s:
        rows = (
            s.query(DeploymentApiKey)
            .filter_by(deployment_id=deployment_id)
            .order_by(DeploymentApiKey.id.desc())
            .all()
        )
        users = {u.id: (u.display_name or u.username) for u in s.query(User).all()}
        return {
            "keys": [
                {
                    "id": r.id,
                    "name": r.name,
                    "prefix": r.key_prefix,
                    "masked": f"{r.key_prefix}…",
                    "status": r.status,
                    "created_by": r.created_by,
                    "created_by_display": users.get(r.created_by, f"#{r.created_by}"),
                    "created_at": r.created_at.isoformat(sep=" ") if r.created_at else "",
                    "last_used_at": r.last_used_at.isoformat(sep=" ") if r.last_used_at else "",
                }
                for r in rows
            ]
        }


def revoke_key(app, deployment_id: int, key_id: int, actor_user_id: int) -> dict:
    with _session(app) as s:
        row = s.get(DeploymentApiKey, key_id)
        if row is None or row.deployment_id != deployment_id:
            raise KeyError("密钥不存在")
        name = row.name
        row.status = "revoked"
        s.commit()
    log_system(
        app, deployment_id, "apikey", actor_user_id=actor_user_id,
        object_kind="apikey", object_pk=str(key_id),
        object_label=f"吊销密钥「{name}」",
        changes={"type": "apikey_revoke", "name": name},
    )
    return {"ok": True}


def authenticate(app, raw_key: str | None) -> dict | None:
    """验签：有效返回 {id, deployment_id, name}；无效/已吊销返回 None。顺带记录 last_used_at。"""
    if not raw_key or not raw_key.startswith(KEY_PREFIX):
        return None
    candidates: list[tuple[int, int, str, str]] = []
    with _session(app) as s:
        rows = (
            s.query(DeploymentApiKey)
            .filter_by(key_prefix=raw_key[:_PREFIX_LEN], status="active")
            .all()
        )
        cipher = app.extensions["cipher"]
        for r in rows:
            try:
                plain = cipher.decrypt(r.key_cipher)
            except Exception:  # noqa: BLE001 密文损坏 → 跳过
                continue
            if hmac.compare_digest(plain, raw_key):
                candidates.append((r.id, r.deployment_id, r.name, r.key_prefix))
        if not candidates:
            return None
        key_id, deployment_id, name, _prefix = candidates[0]
        row = s.get(DeploymentApiKey, key_id)
        if row is not None:
            row.last_used_at = datetime.now().replace(microsecond=0)
            s.commit()
    return {"id": key_id, "deployment_id": deployment_id, "name": name}
