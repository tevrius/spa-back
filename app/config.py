from datetime import date, datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://warehouse:warehouse_demo@db:5432/warehouse"
    app_timezone: str = "Europe/Moscow"
    expiry_warning_days: int = Field(default=30, ge=0)
    inactivity_days: int = Field(default=90, ge=1)
    seed_demo: bool = True

    @field_validator("app_timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()


def business_today() -> date:
    return datetime.now(ZoneInfo(get_settings().app_timezone)).date()
