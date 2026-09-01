"""Application configuration."""

import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    """Base configuration."""

    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")

    # Database
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "sqlite:///low_carbon_screener.db"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    # Redis
    REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    CACHE_TTL = int(os.environ.get("CACHE_TTL", 300))

    # External APIs — TradingView scanner (tvkit / REST)
    TRADINGVIEW_ENABLED = os.environ.get("TRADINGVIEW_ENABLED", "true").lower() == "true"
    TRADINGVIEW_TIMEOUT = int(os.environ.get("TRADINGVIEW_TIMEOUT", 15))
    TVKIT_AUTH_TOKEN = os.environ.get("TVKIT_AUTH_TOKEN", "")
    LIVE_QUOTES = os.environ.get("LIVE_QUOTES", "true").lower() == "true"
    # Optional HTTP proxy just for TradingView, e.g. http://127.0.0.1:7890
    TRADINGVIEW_PROXY = os.environ.get("TRADINGVIEW_PROXY", "")

    # Clarity AI SFDR (official REST only — no HTML scraping)
    CLARITY_API_KEY = os.environ.get("CLARITY_API_KEY", "")
    CLARITY_API_SECRET = os.environ.get("CLARITY_API_SECRET", "")
    CLARITY_BASE_URL = os.environ.get(
        "CLARITY_BASE_URL", "https://api.clarity.ai/clarity/v1"
    )
    CLARITY_JOB_TIMEOUT = int(os.environ.get("CLARITY_JOB_TIMEOUT", 180))

    # Daily close job (America/New_York). Carbon job runs on the 1st of each month.
    SCHEDULER_ENABLED = os.environ.get("SCHEDULER_ENABLED", "true").lower() == "true"
    MARKET_TIMEZONE = os.environ.get("MARKET_TIMEZONE", "America/New_York")
    MARKET_SYNC_CRON = os.environ.get("MARKET_SYNC_CRON", "16:45")

    # Legacy (unused for carbon; kept so old .env files still load)
    BAVEST_API_KEY = os.environ.get("BAVEST_API_KEY", "")
    BAVEST_BASE_URL = os.environ.get("BAVEST_BASE_URL", "https://api.bavest.co")
    INTRINIO_API_KEY = os.environ.get("INTRINIO_API_KEY", "")

    # Pagination
    DEFAULT_PAGE_SIZE = int(os.environ.get("DEFAULT_PAGE_SIZE", 50))
    MAX_PAGE_SIZE = int(os.environ.get("MAX_PAGE_SIZE", 200))

    # Password-reset email (Resend HTTPS, or SMTP). Empty = log the code in DEBUG.
    RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
    EMAIL_FROM = os.environ.get("EMAIL_FROM", "低碳价值筛选器 <noreply@lowcarbon.io>")
    SMTP_HOST = os.environ.get("SMTP_HOST", "")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
    SMTP_USER = os.environ.get("SMTP_USER", "")
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
    SMTP_USE_TLS = os.environ.get("SMTP_USE_TLS", "true").lower() == "true"


class DevelopmentConfig(Config):
    """Development configuration."""

    DEBUG = True
    ENV = "development"


class ProductionConfig(Config):
    """Production configuration."""

    DEBUG = False
    ENV = "production"


class TestingConfig(Config):
    """Testing configuration."""

    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    DEBUG = True
    TRADINGVIEW_ENABLED = False
    SCHEDULER_ENABLED = False
    LIVE_QUOTES = False


config_map = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}


def get_config():
    """Return the config class based on FLASK_ENV."""
    env = os.environ.get("FLASK_ENV", "development")
    return config_map.get(env, DevelopmentConfig)
