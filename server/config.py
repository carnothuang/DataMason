"""配置：环境变量 + 首次启动向导（DR-10，M0 环境变量版）。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class Config:
    """M0 配置：SQLite 元数据库 + 环境变量驱动的初始化。"""

    app_version: str = "0.1.0-M0"
    meta_db_uri: str = "sqlite:///./datamason_meta.db"
    secret_key: str = "dev-secret-change-me"
    # 首次启动向导（环境变量版，DR-10）：管理员账号 + LLM 配置
    admin_username: str = ""
    admin_password: str = ""
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    # 数据根目录：部署的独立物理库（SQLite 文件）存放处
    data_root: str = "./data"
    # 0.11.0 M5-B：文件/导入上限（DR-33）
    max_content_length: int = 20 * 1024 * 1024  # 单请求上限（Flask MAX_CONTENT_LENGTH）
    upload_max_file_bytes: int = 10 * 1024 * 1024  # 单附件上限
    upload_allowed_ext: str = (
        ".png,.jpg,.jpeg,.gif,.webp,.bmp,.svg,.pdf,.txt,.csv,.json,.log,.md,"
        ".doc,.docx,.xls,.xlsx,.ppt,.pptx,.zip,.rar,.7z"
    )
    upload_allowed_mime: str = (
        "image/png,image/jpeg,image/gif,image/webp,image/bmp,image/svg+xml,"
        "application/pdf,text/plain,text/csv,application/json,"
        "application/msword,"
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document,"
        "application/vnd.ms-excel,"
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,"
        "application/vnd.ms-powerpoint,"
        "application/vnd.openxmlformats-officedocument.presentationml.presentation,"
        "application/zip,application/vnd.rar,application/x-7z-compressed,"
        "text/html"
    )
    import_max_rows: int = 5000
    export_max_rows: int = 20000
    import_tmp_ttl_seconds: int = 3600
    extra: dict = field(default_factory=dict)

    def as_flask_config(self) -> dict:
        return {
            "APP_VERSION": self.app_version,
            "SQLALCHEMY_DATABASE_URI": self.meta_db_uri,
            "SECRET_KEY": self.secret_key,
            "DATA_ROOT": self.data_root,
            "MAX_CONTENT_LENGTH": self.max_content_length,
            "DM_UPLOAD_MAX_FILE_BYTES": self.upload_max_file_bytes,
            "DM_UPLOAD_ALLOWED_EXT": self.upload_allowed_ext,
            "DM_UPLOAD_ALLOWED_MIME": self.upload_allowed_mime,
            "DM_IMPORT_MAX_ROWS": self.import_max_rows,
            "DM_EXPORT_MAX_ROWS": self.export_max_rows,
            "DM_IMPORT_TMP_TTL_SECONDS": self.import_tmp_ttl_seconds,
            "DATAMASON_ADMIN_USERNAME": self.admin_username,
            "DATAMASON_ADMIN_PASSWORD": self.admin_password,
            "DATAMASON_LLM_BASE_URL": self.llm_base_url,
            "DATAMASON_LLM_API_KEY": self.llm_api_key,
            "DATAMASON_LLM_MODEL": self.llm_model,
            **self.extra,
        }


def get_config(config_object: object | None = None) -> Config:
    if config_object is not None:
        if isinstance(config_object, Config):
            return config_object
        return Config(**config_object)
    return Config(
        admin_username=os.environ.get("DATAMASON_ADMIN_USERNAME", ""),
        admin_password=os.environ.get("DATAMASON_ADMIN_PASSWORD", ""),
        llm_base_url=os.environ.get("DATAMASON_LLM_BASE_URL", ""),
        llm_api_key=os.environ.get("DATAMASON_LLM_API_KEY", ""),
        llm_model=os.environ.get("DATAMASON_LLM_MODEL", ""),
    )
