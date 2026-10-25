"""Shared configuration; no credentials are read or copied by the backend."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="VISAPILOT_", env_file=".env", extra="ignore"
    )

    demo_mode: bool = True
    data_dir: Path = Path("local_data")
    frontend_dist: Path | None = None
    allowed_origins: list[str] = ["http://127.0.0.1:8000", "http://localhost:8000"]
    cookie_secure: bool = False
    session_cookie_name: str = "visapilot_session"
    session_ttl_seconds: int = Field(default=12 * 60 * 60, gt=0)
    max_upload_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    max_document_pages: int = Field(default=10, gt=0)
    max_image_pixels: int = Field(default=20_000_000, gt=0)
    document_timeout_seconds: int = Field(default=60, gt=0)
    ai_timeout_seconds: int = Field(default=60, gt=0)
    ai_enabled: bool = False
    ai_model: str | None = None

    @property
    def database_path(self) -> Path:
        return self.data_dir / "visapilot.sqlite3"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def temporary_dir(self) -> Path:
        return self.data_dir / "tmp"
