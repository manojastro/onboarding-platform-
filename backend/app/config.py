"""Deployment settings (connection strings, paths). Product policy is NOT here;
it is hardcoded in ``app.policy``."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    database_url: str = "postgresql+psycopg://onboarding:onboarding@localhost:55432/onboarding"
    upload_dir: str = "./data/uploads"
    # Single-operator scope (DECISIONS.md D3): this name fills approved_by and audit actor.
    operator_name: str = "operator"
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]


settings = Settings()
