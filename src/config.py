from pydantic_settings import BaseSettings
from functools import lru_cache

class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://loomuser:secret_password@localhost:5432/loomhub"
    hetzner_api_key: str = ""
    hetzner_base_url: str = "https://inference.hetzner.com/api/v1"
    hetzner_model: str = "Qwen/Qwen3.6-35B-A3B-FP8"
    debug: bool = False
    admin_token: str = ""
    model_config = {"env_file": ".env"}

@lru_cache()
def get_settings() -> Settings:
    return Settings()

settings = get_settings()
