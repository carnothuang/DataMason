"""凭证加密：连接串、LLM api_key 落库前对称加密（Fernet）。

密钥来源（优先级）：
1. 环境变量 DATAMASON_FERNET_KEY（urlsafe base64，32 字节）
2. 数据目录 data_root/.secret_key（首次自动生成，本机持有）
密钥不落业务库、不进版本库。
"""
from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class CryptoError(RuntimeError):
    pass


def _load_or_create_key(data_root: str, env_key: str | None) -> bytes:
    key = env_key or os.environ.get("DATAMASON_FERNET_KEY", "")
    if key:
        return key.encode()
    key_file = Path(data_root) / ".secret_key"
    if key_file.exists():
        return key_file.read_bytes().strip()
    key_file.parent.mkdir(parents=True, exist_ok=True)
    new_key = Fernet.generate_key()
    key_file.write_bytes(new_key)
    return new_key


class Cipher:
    def __init__(self, data_root: str, env_key: str | None = None):
        self._fernet = Fernet(_load_or_create_key(data_root, env_key))

    def encrypt(self, plaintext: str) -> str:
        if plaintext is None or plaintext == "":
            return ""
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        if not token:
            return ""
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise CryptoError("凭证解密失败：密钥不匹配或数据损坏") from exc
