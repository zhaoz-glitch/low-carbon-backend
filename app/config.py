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

    # External APIs
    TRADINGVIEW_ENABLED = os.environ.get("TRADINGVIEW_ENABLED", "true").lower() == "true"
    BAVEST_API_KEY = os.environ.get("BAVEST_API_KEY", "")
    BAVEST_BASE_URL = os.environ.get("BAVEST_BASE_URL", "https://api.bavest.co")
    INTRINIO_API_KEY = os.environ.get("INTRINIO_API_KEY", "")

    # Pagination
    DEFAULT_PAGE_SIZE = int(os.environ.get("DEFAULT_PAGE_SIZE", 50))
    MAX_PAGE_SIZE = int(os.environ.get("MAX_PAGE_SIZE", 200))


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


config_map = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}


def get_config():
    """Return the config class based on FLASK_ENV."""
    env = os.environ.get("FLASK_ENV", "development")
    return config_map.get(env, DevelopmentConfig)
