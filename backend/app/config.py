from functools import lru_cache
import re

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        protected_namespaces=(),
    )

    mongodb_url: str = "mongodb://localhost:27017"
    mongodb_db_name: str = "meiacoin"
    mongodb_timeout_ms: int = 5000

    host: str = "127.0.0.1"
    port: int = 8004
    api_root_path: str = ""
    cors_origins: str = "https://tossemideia.cloud"

    timer_feed_url: str = "https://meiaum.vinnytasso.com.br/api/v1/timer"
    timer_stream_url: str = "https://meiaum.vinnytasso.com.br/api/v1/timer/stream"
    timer_stream_enabled: bool = True
    timer_poll_seconds: int = 15
    timer_stale_seconds: int = 180
    timer_degraded_seconds: int = 45
    pause_credit_tolerance_seconds: int = 60
    attribution_window_seconds: int = 90

    model_alpha: float = 1.0
    model_kappa: float = 0.05
    model_window_minutes: float = 60.0
    model_anchor: str = "peak"

    @field_validator("mongodb_url")
    @classmethod
    def validate_mongodb_url(cls, v: str) -> str:
        if not re.match(r"^mongodb(\+srv)?:\/\/", v):
            raise ValueError("Invalid MongoDB connection string format")
        return v

    @property
    def is_timer_configured(self) -> bool:
        return bool(self.timer_feed_url)

    def public_model_constants(self) -> dict:
        return {
            "alpha": self.model_alpha,
            "kappa": self.model_kappa,
            "window_minutes": self.model_window_minutes,
            "anchor": self.model_anchor,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
