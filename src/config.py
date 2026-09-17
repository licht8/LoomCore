from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://loomuser:secret_password@localhost:5432/loomcore"
    hetzner_api_key: str = ""
    hetzner_base_url: str = "https://inference.hetzner.com/api/v1"
    hetzner_model: str = "Qwen/Qwen3.6-35B-A3B-FP8"
    hetzner_connect_timeout: float = 15.0
    hetzner_read_timeout: float = 90.0
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
    # Which admin credential paths are accepted: "token", "jwt", "both", or ""
    # to auto-select ("jwt" when password+TOTP is configured, else "token").
    auth_mode: str = ""
    secure_cookies: bool = True
    require_tls: bool = False
    loomcore_revoked_file: str = "/data/revoked.json"
    trusted_proxies: str = "127.0.0.1,::1"
    llm_daily_call_limit: int = 1000
    plugins_dir: str = "/app/plugins"
    plugins_state_file: str = ""
    confirmations_file: str = ""
    timing_enabled: bool = Field(default=False, validation_alias="LOOMCORE_TIMING")
    model_config = {"env_file": ".env"}

    @model_validator(mode="after")
    def _require_session_secret_for_jwt(self) -> "Settings":
        """Refuse to start with JWT enabled but an empty signing key.

        Password+TOTP enables JWT verification, which signs and validates with
        ``SESSION_SECRET``. An empty secret would accept tokens forged with the
        empty HMAC key, so fail fast instead of silently trusting it. An
        explicit ``AUTH_MODE=token`` disables JWT verification entirely, so
        leftover credentials are allowed there without a secret.
        """
        jwt_disabled = self.auth_mode.strip().lower() == "token"
        if (
            not jwt_disabled
            and self.admin_password_hash
            and self.admin_totp_secret
            and not self.session_secret
        ):
            raise ValueError(
                "SESSION_SECRET must be set when ADMIN_PASSWORD_HASH and "
                "ADMIN_TOTP_SECRET are configured"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()

settings = get_settings()
