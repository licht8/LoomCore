from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://loomuser:secret_password@localhost:5432/loomcore"
    hetzner_api_key: str = ""
    hetzner_base_url: str = "https://inference.hetzner.com/api/v1"
    hetzner_model: str = "Qwen/Qwen3.6-35B-A3B-FP8"
    hetzner_connect_timeout: float = 15.0
    hetzner_read_timeout: float = 300.0
    hetzner_write_timeout: float = 30.0
    hetzner_pool_timeout: float = 15.0
    hetzner_max_retries: int = 2
    hetzner_enable_thinking: bool = False
    debug: bool = False
    admin_token: str = ""
    admin_username: str = "admin"
    admin_password_hash: str = ""
    admin_totp_secret: str = ""
    session_secret: str = ""
    plugins_dir: str = "/app/plugins"
    plugins_state_file: str = ""
    confirmations_file: str = ""
    timing_enabled: bool = Field(default=False, validation_alias="LOOMCORE_TIMING")
    model_config = {"env_file": ".env"}

@lru_cache
def get_settings() -> Settings:
    return Settings()

settings = get_settings()
