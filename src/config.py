from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode

# Deliberate credential-free placeholder. Deployments must set DATABASE_URL
# (compose derives it from POSTGRES_*); a forgotten override then fails at
# connect time instead of silently using well-known credentials.
DEFAULT_DATABASE_URL = "postgresql+asyncpg://localhost:5432/loomcore"


class Settings(BaseSettings):
    database_url: str = DEFAULT_DATABASE_URL
    # Optional LLM env bootstrap. All empty by default so a plain deployment
    # starts unconfigured and the admin picks a provider in Settings.
    llm_provider: str = ""
    llm_base_url: str = ""
    llm_model: str = ""
    llm_api_key: str = ""
    llm_summary_model: str = ""
    llm_planner_model: str = ""
    llm_connect_timeout: float = 15.0
    llm_read_timeout: float = 30.0
    llm_write_timeout: float = 30.0
    llm_pool_timeout: float = 15.0
    llm_max_retries: int = 1
    llm_planner_max_tokens: int = 1024
    llm_summary_max_tokens: int = 512
    # 0 disables the extra bound: the summary then uses the normal read timeout.
    llm_summary_timeout: float = 0.0
    llm_enable_thinking: bool = False
    llm_thinking_template: bool = False
    # Skip a reasoning model's thinking step for the short post-tool summary.
    llm_summary_disable_thinking: bool = True
    llm_settings_file: str = ""
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
    cors_origins: Annotated[list[str], NoDecode] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
    llm_daily_call_limit: int = 1000
    plugins_dir: str = "/app/plugins"
    # Plugin trust boundary: comma/space-separated plugin ids allowed to launch
    # (empty = launch every discovered plugin). Plugins run unsandboxed as root.
    plugins_allowed: str = Field(
        default="", validation_alias="LOOMCORE_PLUGINS_ALLOWED"
    )
    plugins_state_file: str = ""
    confirmations_file: str = ""
    timing_enabled: bool = Field(default=False, validation_alias="LOOMCORE_TIMING")
    # Comma/space-separated allow-list of read-only command basenames whose
    # ops_run_command turn skips the summary. Empty (default) disables it.
    report_commands: str = Field(
        default="", validation_alias="LOOMCORE_REPORT_COMMANDS"
    )
    model_config = {"env_file": ".env"}

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, value: object) -> object:
        """Accept a comma/space-separated env string or an explicit list.

        ``CORS_ORIGINS`` is a list setting but Docker compose passes it as a
        single ``${VAR:-default}`` string, so split a raw string on commas and
        whitespace (matching the ``TRUSTED_PROXIES`` convention) and pass an
        already-parsed list through unchanged.
        """
        if isinstance(value, str):
            return [item for item in value.replace(",", " ").split() if item]
        return value

    @model_validator(mode="after")
    def _require_session_secret_for_jwt(self) -> "Settings":
        """Refuse to start with JWT enabled but an empty signing key.

        JWT verification signs and validates with ``SESSION_SECRET`` whenever
        JWT is active: an explicit ``AUTH_MODE`` of ``jwt``/``both``, or the
        inferred mode when password+TOTP is configured. An empty secret would
        accept tokens forged with the empty HMAC key, so fail fast instead of
        silently trusting it. An explicit ``AUTH_MODE=token`` disables JWT
        verification entirely, so leftover credentials are allowed there
        without a secret.
        """
        configured_mode = self.auth_mode.strip().lower()
        if configured_mode in {"jwt", "both"}:
            jwt_enabled = True
        elif configured_mode == "token":
            jwt_enabled = False
        else:
            jwt_enabled = bool(self.admin_password_hash and self.admin_totp_secret)
        if jwt_enabled and not self.session_secret.strip():
            raise ValueError(
                "SESSION_SECRET must be set when JWT authentication is enabled"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()

settings = get_settings()
