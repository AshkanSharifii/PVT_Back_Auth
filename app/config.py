from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "PVT Back Auth"
    app_env: str = "development"
    debug: bool = True
    api_prefix: str = "/api/v1"
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    trusted_hosts: str = "localhost,127.0.0.1,test,testserver"

    database_url: str = "postgresql+asyncpg://pvt:pvt@127.0.0.1:5432/pvt_auth"
    db_pool_size: int = 10
    db_max_overflow: int = 20
    redis_url: str = "redis://127.0.0.1:6379/0"
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_enabled: bool = False
    kafka_topic_auth_events: str = "auth.events"

    jwt_secret: str = "change-me-in-production-use-long-random-string"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 14

    # Cookie-based refresh (HttpOnly). Access token still returned in JSON body.
    refresh_cookie_name: str = "pvt_refresh"
    cookie_secure: bool = False
    cookie_samesite: str = "lax"
    cookie_domain: str = ""

    password_min_length: int = 8
    otp_ttl_seconds: int = 600
    otp_max_attempts: int = 5
    otp_demo_mode: bool = True
    otp_demo_code: str = "123456"
    invite_token_ttl_seconds: int = 604800

    rate_limit_login_per_minute: int = 10
    rate_limit_otp_per_minute: int = 3

    # Optional short Redis cache for /me
    me_cache_ttl_seconds: int = 20

    seed_on_startup: bool = True
    # Require MFA for admin/owner logins once enabled on the account.
    mfa_issuer: str = "TebNote"

    # --- Kavenegar SMS (fill API key yourself) ---
    kavenegar_api_key: str = ""
    kavenegar_sender: str = ""
    kavenegar_otp_template: str = ""
    kavenegar_otp_message_template: str = "TebNote code: {code}"
    kavenegar_timeout_seconds: float = 15.0
    # SMS template when admin creates a user with password delivery.
    kavenegar_credentials_message_template: str = (
        "TebNote access\nEmail: {email}\nPassword: {password}\nLogin and change your password."
    )

    # --- ZarinPal subscription payments (fill merchant id yourself) ---
    zarinpal_merchant_id: str = ""
    zarinpal_sandbox: bool = True
    zarinpal_callback_url: str = "http://127.0.0.1:8000/api/v1/subscriptions/zarinpal/callback"
    zarinpal_currency: str = "IRR"  # IRR (rial) or IRT (toman)

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in {"production", "prod"}

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def trusted_host_list(self) -> list[str]:
        return [item.strip() for item in self.trusted_hosts.split(",") if item.strip()]

    @property
    def sms_live(self) -> bool:
        return bool(self.kavenegar_api_key.strip())

    @property
    def zarinpal_live(self) -> bool:
        return bool(self.zarinpal_merchant_id.strip())

    @property
    def zarinpal_request_url(self) -> str:
        if self.zarinpal_sandbox or not self.zarinpal_live:
            return "https://sandbox.zarinpal.com/pg/v4/payment/request.json"
        return "https://payment.zarinpal.com/pg/v4/payment/request.json"

    @property
    def zarinpal_verify_url(self) -> str:
        if self.zarinpal_sandbox or not self.zarinpal_live:
            return "https://sandbox.zarinpal.com/pg/v4/payment/verify.json"
        return "https://payment.zarinpal.com/pg/v4/payment/verify.json"

    @property
    def zarinpal_startpay_base(self) -> str:
        if self.zarinpal_sandbox or not self.zarinpal_live:
            return "https://sandbox.zarinpal.com/pg/StartPay/"
        return "https://payment.zarinpal.com/pg/StartPay/"

    @property
    def allow_otp_demo(self) -> bool:
        """Never expose demo OTP codes in production or when SMS is live."""
        if self.is_production or self.sms_live:
            return False
        return self.otp_demo_mode

    @property
    def effective_cookie_secure(self) -> bool:
        return self.cookie_secure or self.is_production

    @model_validator(mode="after")
    def harden_production(self) -> "Settings":
        if self.is_production:
            if len(self.jwt_secret) < 32 or self.jwt_secret.startswith("change-me"):
                raise ValueError(
                    "JWT_SECRET must be a long random string (>=32 chars) in production."
                )
            if self.debug:
                object.__setattr__(self, "debug", False)
            if "*" in self.cors_origins:
                raise ValueError("CORS_ORIGINS must not include '*' in production.")
            if self.otp_demo_mode:
                object.__setattr__(self, "otp_demo_mode", False)
            if self.access_token_expire_minutes > 30:
                object.__setattr__(self, "access_token_expire_minutes", 15)
            if self.password_min_length < 8:
                object.__setattr__(self, "password_min_length", 8)
        return self

    @field_validator("cookie_samesite")
    @classmethod
    def validate_samesite(cls, value: str) -> str:
        normalized = value.lower()
        if normalized not in {"lax", "strict", "none"}:
            raise ValueError("COOKIE_SAMESITE must be lax, strict, or none.")
        return normalized


@lru_cache
def get_settings() -> Settings:
    return Settings()
