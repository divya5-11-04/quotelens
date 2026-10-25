import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    db_path: str = "quotelens.db"
    upload_dir: str = "uploads"
    max_upload_mb: int = 10
    api_token: str = ""
    anthropic_api_key: str = ""
    llm_model: str = "claude-sonnet-4-6"

    @classmethod
    def from_env(cls):
        env = os.environ.get
        return cls(db_path=env("QUOTELENS_DB", "quotelens.db"), upload_dir=env("QUOTELENS_UPLOADS", "uploads"),
                   max_upload_mb=int(env("QUOTELENS_MAX_UPLOAD_MB", "10")), api_token=env("QUOTELENS_API_TOKEN", ""),
                   anthropic_api_key=env("ANTHROPIC_API_KEY", ""), llm_model=env("QUOTELENS_LLM_MODEL", "claude-sonnet-4-6"))
